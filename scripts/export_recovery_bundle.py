#!/usr/bin/env python3
"""Create a local encrypted recovery artifact; plaintext stays temporary."""
from __future__ import annotations
import argparse, hashlib, json, shlex, subprocess, tarfile, tempfile
from pathlib import Path
from typing import Mapping

REQUIRED_RECOVERY_RECORDS=("holdings","transactions","commands","runs","packets","reports","publications","roles","schema_version")
DATASET_FIELDS={"holdings":{"ticker":str,"shares":str,"average_cost":str},"transactions":{"id":str,"ticker":str,"quantity":str,"price":str},"commands":{"id":str,"status":str},"runs":{"id":str,"status":str,"phase":str},"packets":{"id":str,"run_id":str,"packet_hash":str},"reports":{"id":str,"run_id":str,"report_hash":str,"rendered_hash":str},"publications":{"report_id":str,"status":str,"telegram_message_ids":list},"roles":{"role":str,"login":bool},"schema_version":{"version":str,"sha256":str}}
def canonical_json(value:object)->str:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def _validated_records(records:Mapping[str,object])->dict[str,list[dict[str,object]]]:
 if set(records)!=set(REQUIRED_RECOVERY_RECORDS): raise ValueError("recovery records must contain the exact required record sets")
 result={}
 for name,fields in DATASET_FIELDS.items():
  rows=records[name]
  if not isinstance(rows,list) or (name in {"holdings","runs","reports"} and not rows): raise ValueError(f"recovery record set {name} requires meaningful rows")
  clean=[]
  for row in rows:
   if not isinstance(row,Mapping) or set(row)!=set(fields) or any(not isinstance(row[k],t) for k,t in fields.items()): raise ValueError(f"recovery record {name} has unknown or invalid fields")
   clean.append(dict(row))
  result[name]=sorted(clean,key=canonical_json)
 return result
def _command(template:str,input_path:Path,output_path:Path)->None:
 if "{input}" not in template or "{output}" not in template: raise ValueError("encryption and decryption commands require {input} and {output}")
 result=subprocess.run([p.format(input=str(input_path),output=str(output_path)) for p in shlex.split(template)],check=False,capture_output=True,text=True)
 if result.returncode: raise RuntimeError("caller-supplied encryption command failed")
def export_recovery_bundle(records:Mapping[str,object],destination:Path,*,encrypt_command:str|None=None,decrypt_command:str|None=None)->Path:
 if not encrypt_command or not decrypt_command: raise ValueError("encryption and verification commands are required")
 if destination.exists(): raise ValueError("recovery artifact destination must not already exist")
 normalized=_validated_records(records)
 with tempfile.TemporaryDirectory(prefix="stocks-recovery-") as temporary:
  root=Path(temporary)/"payload"; data=root/"data"; data.mkdir(parents=True); files={}
  for name,rows in normalized.items():
   raw="".join(canonical_json(row)+"\n" for row in rows).encode(); (data/f"{name}.ndjson").write_bytes(raw); files[name]={"path":f"data/{name}.ndjson","sha256":hashlib.sha256(raw).hexdigest(),"records":len(rows)}
  core={"format":"stocks-agent-recovery-v2","record_sets":list(REQUIRED_RECOVERY_RECORDS),"files":files,"secrets_included":False,"portfolio_values_in_manifest":False}; manifest={**core,"root_hash":hashlib.sha256(canonical_json(core).encode()).hexdigest()}; (root/"manifest.json").write_text(canonical_json(manifest)+"\n")
  archive=Path(temporary)/"payload.tar"
  with tarfile.open(archive,"w") as tar: tar.add(root,arcname="payload")
  _command(encrypt_command,archive,destination)
  if not destination.is_file() or not destination.stat().st_size: raise RuntimeError("caller-supplied encryption artifact is unavailable")
  verified=Path(temporary)/"verified.tar"; _command(decrypt_command,destination,verified)
  if not verified.is_file() or hashlib.sha256(archive.read_bytes()).digest()!=hashlib.sha256(verified.read_bytes()).digest(): raise RuntimeError("caller-supplied decrypt verification failed")
  destination.with_suffix(destination.suffix+".receipt.json").write_text(canonical_json({"format":"stocks-agent-recovery-v2","root_hash":manifest["root_hash"],"artifact_sha256":hashlib.sha256(destination.read_bytes()).hexdigest()})+"\n")
 return destination
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("--records-json",type=Path,required=True);p.add_argument("--destination",type=Path,required=True);p.add_argument("--encrypt-command",required=True);p.add_argument("--decrypt-command",required=True);a=p.parse_args();export_recovery_bundle(json.loads(a.records_json.read_text()),a.destination,encrypt_command=a.encrypt_command,decrypt_command=a.decrypt_command);return 0
if __name__=="__main__":raise SystemExit(main())
