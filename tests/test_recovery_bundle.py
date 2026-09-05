import json
import sys
import pytest
from scripts.export_recovery_bundle import export_recovery_bundle
from scripts.verify_recovery_bundle import verify_recovery_bundle

COPY=f"{sys.executable} -c \"import shutil,sys;shutil.copyfile(sys.argv[1],sys.argv[2])\" {{input}} {{output}}"
def recovery_records():
 return {"holdings":[{"ticker":"VTI","shares":"2","average_cost":"100"}],"transactions":[{"id":"t1","ticker":"VTI","quantity":"2","price":"100"}],"commands":[{"id":"c1","status":"recorded"}],"runs":[{"id":"r1","status":"completed","phase":"post-market"}],"packets":[{"id":"p1","run_id":"r1","packet_hash":"a"*64}],"reports":[{"id":"rp1","run_id":"r1","report_hash":"b"*64,"rendered_hash":"c"*64}],"publications":[{"report_id":"rp1","status":"suppressed","telegram_message_ids":[]}],"roles":[{"role":"stock_agent_dashboard","login":False}],"schema_version":[{"version":"20260912","sha256":"d"*64}]}
def isolated(records): return {"identity":{"environment":"isolated_restore","connection_id":"11111111-1111-4111-8111-111111111111","read_only":True},"records":records}
def test_recovery_export_is_encrypted_and_manifest_redacted(tmp_path):
 records=recovery_records(); artifact=export_recovery_bundle(records,tmp_path/"bundle.enc",encrypt_command=COPY,decrypt_command=COPY)
 sidecar=json.loads(artifact.with_suffix(".enc.receipt.json").read_text());assert sidecar["root_hash"] and "VTI" not in json.dumps(sidecar)
 assert verify_recovery_bundle(artifact,isolated(records),decrypt_command=COPY)["status"]=="verified"
def test_recovery_rejects_unknown_fields_empty_critical_sets_and_nonisolated_readback(tmp_path):
 bad=recovery_records();bad["roles"][0]["password"]="secret"
 with pytest.raises(ValueError,match="unknown"): export_recovery_bundle(bad,tmp_path/"bad.enc",encrypt_command=COPY,decrypt_command=COPY)
 bad=recovery_records();bad["reports"]=[]
 with pytest.raises(ValueError,match="meaningful"): export_recovery_bundle(bad,tmp_path/"empty.enc",encrypt_command=COPY,decrypt_command=COPY)
 artifact=export_recovery_bundle(recovery_records(),tmp_path/"bundle.enc",encrypt_command=COPY,decrypt_command=COPY)
 with pytest.raises(RuntimeError,match="isolated"): verify_recovery_bundle(artifact,recovery_records(),decrypt_command=COPY)
def test_recovery_requires_commands_and_detects_tampered_isolated_readback(tmp_path):
 records=recovery_records()
 with pytest.raises(ValueError,match="encryption"): export_recovery_bundle(records,tmp_path/"plain")
 artifact=export_recovery_bundle(records,tmp_path/"bundle.enc",encrypt_command=COPY,decrypt_command=COPY);changed=recovery_records();changed["reports"][0]["report_hash"]="e"*64
 with pytest.raises(RuntimeError,match="restored"): verify_recovery_bundle(artifact,isolated(changed),decrypt_command=COPY)
