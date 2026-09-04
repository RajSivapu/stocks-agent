#!/usr/bin/env python3
"""Fail closed unless every V1 release receipt is typed and reconciled."""
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

REQUIRED_GATES=("exact_head_ci","independent_review","quota_receipts","dry_run_zero_writes","dry_run_zero_sends","migration_version","gateway_version","dashboard_api_version","site_version","owner_canary","anonymous_denial","non_owner_denial","source_parity","scheduled_receipt","rollback_check")
SHA=re.compile(r"[0-9a-f]{40}"); HASH=re.compile(r"[0-9a-f]{64}"); UUID=re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
def _row(r:Mapping[str,object],g:str)->Mapping[str,object]:
 v=r.get(g)
 if not isinstance(v,Mapping): raise RuntimeError(f"invalid release gate: {g}")
 return v
def _same(r:Mapping[str,object], fields:tuple[str,...], sha:str,g:str):
 if any(r.get(f)!=sha for f in fields): raise RuntimeError(f"release gate SHA mismatch: {g}")
def _hash(v:object)->bool:return isinstance(v,str) and HASH.fullmatch(v) is not None
def _uuid(v:object)->bool:return isinstance(v,str) and UUID.fullmatch(v) is not None
def _reject_sensitive(value:object):
 if isinstance(value,Mapping):
  for key,nested in value.items():
   if str(key).lower() in {"secret","password","database_url","access_token","service_key","telegram_token"}: raise RuntimeError("release receipt contains sensitive fields")
   _reject_sensitive(nested)
 elif isinstance(value,list):
  for nested in value:_reject_sensitive(nested)

def verify_release(receipt:Mapping[str,object])->dict[str,object]:
 _reject_sensitive(receipt)
 for gate in REQUIRED_GATES:
  if gate not in receipt: raise RuntimeError(f"missing release gate: {gate}")
 candidate=receipt.get("candidate_sha")
 if not isinstance(candidate,str) or not SHA.fullmatch(candidate): raise RuntimeError("missing release gate: candidate_sha")
 ci=_row(receipt,"exact_head_ci"); review=_row(receipt,"independent_review")
 if ci.get("status")!="passed" or ci.get("conclusion")!="success" or isinstance(ci.get("workflow_run_id"),bool) or not isinstance(ci.get("workflow_run_id"),int): raise RuntimeError("invalid release gate: exact_head_ci")
 _same(ci,("candidate_sha","workflow_sha"),candidate,"exact_head_ci")
 if review.get("status")!="passed" or review.get("verdict")!="approved": raise RuntimeError("invalid release gate: independent_review")
 _same(review,("candidate_sha","reviewed_sha"),candidate,"independent_review")
 quota=_row(receipt,"quota_receipts"); reservations=quota.get("provider_reservations")
 if quota.get("status")!="verified" or not isinstance(reservations,Mapping) or not reservations or any(isinstance(v,bool) or not isinstance(v,int) or v<0 for v in reservations.values()) or quota.get("total_requests")!=sum(reservations.values()): raise RuntimeError("invalid release gate: quota_receipts")
 writes=_row(receipt,"dry_run_zero_writes"); deltas=writes.get("table_deltas")
 if writes.get("status")!="verified" or not isinstance(deltas,Mapping) or not deltas or any(v!=0 for v in deltas.values()): raise RuntimeError("dry-run side-effect gate failed")
 sends=_row(receipt,"dry_run_zero_sends")
 if sends.get("status")!="verified" or sends.get("telegram_message_ids")!=[] or sends.get("message_id_delta")!=0: raise RuntimeError("dry-run side-effect gate failed")
 migrations=_row(receipt,"migration_version"); versions=migrations.get("versions")
 if migrations.get("status")!="verified" or not isinstance(versions,list) or [r.get("version") for r in versions if isinstance(r,Mapping)]!=["20260907","20260908"] or len(versions)!=2 or any(not isinstance(r,Mapping) or not _hash(r.get("sha256")) for r in versions): raise RuntimeError("invalid release gate: migration_version")
 for gate in ("gateway_version","dashboard_api_version"):
  row=_row(receipt,gate)
  if row.get("status")!="deployed" or isinstance(row.get("version"),bool) or not isinstance(row.get("version"),int) or row["version"]<=0 or not _hash(row.get("source_sha256")): raise RuntimeError(f"invalid release gate: {gate}")
  _same(row,("candidate_sha","deployed_sha"),candidate,gate)
 site=_row(receipt,"site_version"); parsed=urlparse(str(site.get("url","")))
 if site.get("status")!="deployed" or not site.get("version") or parsed.scheme!="https" or not isinstance(site.get("asset_hashes"),list) or not site["asset_hashes"] or any(not _hash(v) for v in site["asset_hashes"]): raise RuntimeError("invalid release gate: site_version")
 _same(site,("candidate_sha","deployed_sha"),candidate,"site_version")
 for gate,status in (("owner_canary",200),("anonymous_denial",401),("non_owner_denial",403)):
  row=_row(receipt,gate)
  if row.get("status")!="verified" or row.get("http_status")!=status or not isinstance(row.get("url"),str): raise RuntimeError(f"invalid release gate: {gate}")
 parity=_row(receipt,"source_parity"); counts=parity.get("counts"); required={"runs","events","rankings","packets","reports","report_publications"}
 if parity.get("status")!="verified" or parity.get("relationships_verified") is not True or parity.get("hashes_verified") is not True or not isinstance(counts,Mapping) or set(counts)!=required or any(isinstance(counts[k],bool) or not isinstance(counts[k],int) or counts[k]<=0 for k in required): raise RuntimeError("invalid release gate: source_parity")
 _same(parity,("candidate_sha",),candidate,"source_parity")
 scheduled=_row(receipt,"scheduled_receipt"); ids=(scheduled.get("run_id"),scheduled.get("intelligence_run_id"),scheduled.get("packet_id"),scheduled.get("report_id")); pub=scheduled.get("publication_receipt")
 if scheduled.get("status")!="completed" or not all(_uuid(v) for v in ids) or ids[0]!=ids[1] or not isinstance(pub,Mapping) or pub.get("status") not in {"accepted_by_telegram","duplicate"} or not _hash(scheduled.get("packet_hash")) or not _hash(scheduled.get("report_hash")): raise RuntimeError("invalid release gate: scheduled_receipt")
 msg=pub.get("telegram_message_ids")
 if pub.get("status")=="accepted_by_telegram" and (not isinstance(msg,list) or not msg or any(isinstance(v,bool) or not isinstance(v,int) or v<=0 for v in msg)): raise RuntimeError("invalid release gate: scheduled_receipt")
 rollback=_row(receipt,"rollback_check"); gateway=rollback.get("gateway"); runtime=rollback.get("runtime_login")
 if rollback.get("status")!="rolled_back" or rollback.get("function")!="owner-dashboard-api" or rollback.get("dashboard_secrets_unset") != ["DASHBOARD_ALLOWED_ORIGINS","DASHBOARD_DATABASE_URL","DASHBOARD_OWNER_USER_ID"] or not isinstance(runtime,Mapping) or runtime.get("status")!="disabled" or runtime.get("login") is not False or runtime.get("memberships")!=0 or not isinstance(gateway,Mapping) or gateway.get("status")!="restored" or not _hash(gateway.get("source_sha256")) or not SHA.fullmatch(str(gateway.get("git_sha",""))) or isinstance(gateway.get("function_version"),bool) or not isinstance(gateway.get("function_version"),int) or gateway["function_version"]<=0: raise RuntimeError("invalid release gate: rollback_check")
 return {"status":"verified","candidate_sha":candidate,"gate_count":len(REQUIRED_GATES)}

def main()->int:
 parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("receipt",type=Path); args=parser.parse_args()
 try:value=json.loads(args.receipt.read_text())
 except (OSError,json.JSONDecodeError) as error:raise SystemExit("release receipt is unavailable or malformed") from error
 if not isinstance(value,dict):raise SystemExit("release receipt must be a JSON object")
 print(json.dumps(verify_release(value),sort_keys=True,separators=(",",":"))); return 0
if __name__=="__main__":raise SystemExit(main())
