#!/usr/bin/env python3
"""Verify encrypted recovery data against an explicitly identified isolated restore."""
from __future__ import annotations
import argparse,hashlib,json,tarfile,tempfile
from pathlib import Path
from typing import Mapping
from scripts.export_recovery_bundle import REQUIRED_RECOVERY_RECORDS,_validated_records,canonical_json,_command
def verify_recovery_bundle(artifact:Path,isolated_receipt:Mapping[str,object]|None=None,*,decrypt_command:str|None=None)->dict[str,object]:
 if not isinstance(isolated_receipt,Mapping) or not decrypt_command: raise RuntimeError("isolated restored database identity and decrypt verification are required")
 identity=isolated_receipt.get("identity");records=isolated_receipt.get("records")
 if not isinstance(identity,Mapping) or identity.get("environment")!="isolated_restore" or identity.get("read_only") is not True or not isinstance(identity.get("connection_id"),str): raise RuntimeError("isolated restored database identity is unsafe")
 if not isinstance(records,Mapping): raise RuntimeError("isolated restored records are unavailable")
 restored=_validated_records(records)
 with tempfile.TemporaryDirectory(prefix="stocks-recovery-verify-") as temporary:
  plain=Path(temporary)/"payload.tar";_command(decrypt_command,artifact,plain)
  try:
   with tarfile.open(plain) as tar: tar.extractall(Path(temporary)/"out",filter="data")
   root=Path(temporary)/"out/payload";manifest=json.loads((root/"manifest.json").read_text())
  except Exception as error: raise RuntimeError("encrypted recovery payload is malformed") from error
  core={key:manifest.get(key) for key in ("format","record_sets","files","secrets_included","portfolio_values_in_manifest")}
  if manifest.get("root_hash")!=hashlib.sha256(canonical_json(core).encode()).hexdigest() or core["record_sets"]!=list(REQUIRED_RECOVERY_RECORDS) or core["format"]!="stocks-agent-recovery-v2": raise RuntimeError("recovery manifest root hash is invalid")
  for name in REQUIRED_RECOVERY_RECORDS:
   entry=core["files"].get(name) if isinstance(core["files"],Mapping) else None
   if not isinstance(entry,Mapping) or entry.get("path")!=f"data/{name}.ndjson": raise RuntimeError("recovery manifest path is unsafe")
   raw=(root/entry["path"]).read_bytes();expected="".join(canonical_json(row)+"\n" for row in restored[name]).encode()
   if raw!=expected or entry.get("records")!=len(restored[name]) or entry.get("sha256")!=hashlib.sha256(raw).hexdigest(): raise RuntimeError("isolated restored records do not match the recovery bundle")
 return {"status":"verified","isolated":True,"record_set_count":len(REQUIRED_RECOVERY_RECORDS)}
