"""Repository-native PostgreSQL/Supabase release and recovery transport.

Construction performs no I/O. This adapter owns the protected database, runtime
role, managed secrets, and Supabase Edge functions. Native Sites publication is
performed separately by the owner through the Sites connector and yields its own
exact-source receipt; GitHub Actions never receives a Sites write credential.

Snapshots contain base64 file bytes and recoverable credentials. They belong in
the authenticated encrypted journal, never stdout or a public release receipt.
Supabase restore redeploys prior bytes/configuration and allocates a NEW version;
the engine retains both captured and restoration identities.
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
import tempfile
import tomllib

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from scripts.release_components import (BACKEND_COMPONENTS, FUNCTIONS, MANAGED_SECRETS, ROOT, canonical,
    capture_managed_secrets, same_content, validate_snapshot)
from scripts.provision_dashboard_runtime_role import RUNTIME_ROLE, PRIVILEGE_ROLE, runtime_url

CLI = ["npx", "--yes", "supabase@2.116.0"]
ROLE_ATTRIBUTES = ("rolsuper", "rolinherit", "rolcreaterole", "rolcreatedb", "rolcanlogin",
                   "rolreplication", "rolbypassrls", "rolconnlimit", "rolvaliduntil")
JOURNALS = "public.stock_agent_component_recovery_journals"


def absent(configuration=None):
    return {"exists": False, "identity": None, "version": None,
            "configuration": configuration or {}, "files": {}, "values": {}}


def encode_files(root):
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink(): raise RuntimeError("function bytes contain a symlink")
        if path.is_file(): files[path.relative_to(root).as_posix()] = base64.b64encode(path.read_bytes()).decode()
    if not files: raise RuntimeError("downloaded active function bytes are unavailable")
    return files


def function_path(value, name):
    if not isinstance(value, str): raise RuntimeError("exact function configuration is unavailable")
    # Management-plane paths may include this standard source prefix. Do not
    # accept machine-specific absolute paths or dependencies outside the capture.
    value = value.removeprefix("./")
    prefixes = (f"supabase/functions/{name}/", f"functions/{name}/")
    if value.startswith("file://"):
        source_prefixes = tuple(f"/source/{prefix}" for prefix in prefixes)
        matches = [prefix for prefix in source_prefixes if prefix in value]
        if len(matches) != 1 or value.count(matches[0]) != 1:
            raise RuntimeError("exact function configuration has an unsafe path")
        value = value.split(matches[0], 1)[1]
    else:
        for prefix in prefixes:
            if value.startswith(prefix): value = value[len(prefix):]; break
    if (not value or PurePosixPath(value).is_absolute() or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise RuntimeError("exact function configuration has an unsafe path")
    return value


def scram_verifier(password):
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 4096)
    client = hmac.new(salted, b"Client Key", "sha256").digest()
    server = hmac.new(salted, b"Server Key", "sha256").digest()
    b64 = lambda value: base64.b64encode(value).decode()
    return f"SCRAM-SHA-256$4096:{b64(salt)}${b64(hashlib.sha256(client).digest())}:{b64(server)}"


def literal_secret_values(values):
    if any(not isinstance(value, str) or not value or value != value.strip()
           or any(char in value for char in "\r\n\x00\"'\\$#") for value in values.values()):
        raise RuntimeError("managed secret cannot be restored with literal env-file semantics")


class NativeReleaseAdapter:
    def __init__(self, context, *, runner=None, connector=None, environment=None, repo_root=ROOT):
        self.context = dict(context)
        self.runner = runner or subprocess.run
        self.connector = connector or psycopg.connect
        self.environment = dict(os.environ if environment is None else environment)
        self.root = Path(repo_root)
        self.known_secrets = None  # parse only after candidate trust and backend preflight
        self.original = {}
        self.readbacks = {}
        self.captured_at = None
        self.last_journal = None
        self.static_receipt = None

    def _project(self):
        project = self.context.get("project_ref", "")
        if not re.fullmatch(r"[a-z0-9]{20}", project): raise RuntimeError("exact native project reference is required")
        return project

    def _command(self, args, *, cwd=None, json_output=False):
        allowed = ("PATH", "HOME", "TMPDIR", "CI", "NO_COLOR", "NPM_CONFIG_CACHE",
                   "XDG_CONFIG_HOME", "SUPABASE_ACCESS_TOKEN")
        environment = {key: self.environment[key] for key in allowed
                       if isinstance(self.environment.get(key), str)}
        result = self.runner([*CLI, *args, "--project-ref", self._project()],
            cwd=cwd or self.root, capture_output=True, text=True, check=False,
            env=environment)
        if result.returncode != 0:
            # CLI stderr can contain credentials, function bytes, or signed URLs.
            raise RuntimeError("protected Supabase command failed: " + " ".join(args[:2]))
        if not json_output: return None
        try: return json.loads(result.stdout)
        except (ValueError, TypeError) as error: raise RuntimeError("Supabase inventory is malformed") from error

    def _secret_inventory(self):
        rows = self._command(["secrets", "list", "--output", "json"], json_output=True)
        if not isinstance(rows, list):
            raise RuntimeError("managed secret inventory is unavailable")
        inventory = []
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("managed secret inventory is malformed")
            name = row.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise RuntimeError("managed secret inventory is malformed")
            if name not in MANAGED_SECRETS:
                continue
            digest = row.get("value")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RuntimeError("managed secret digest is unavailable")
            inventory.append({"name": name, "digest": digest})
        return inventory

    def managed_secret_digests(self):
        """Return the validated, non-secret digests of the managed runtime values."""
        return copy.deepcopy(self._secret_inventory())

    def _connection(self):
        url = self.environment.get("POSTGRES_URL")
        if not url: raise RuntimeError("protected PostgreSQL administrator endpoint is required")
        return self.connector(url, row_factory=dict_row, connect_timeout=15)

    def _metadata(self, name):
        rows = self._command(["functions", "list", "--output", "json"], json_output=True)
        if not isinstance(rows, list): raise RuntimeError("function inventory is unavailable")
        found = [row for row in rows if row.get("slug", row.get("name")) == name]
        if not found: return None
        if len(found) != 1: raise RuntimeError("function identity is ambiguous")
        row = found[0]
        if (not isinstance(row.get("id"), str) or not row["id"]
                or type(row.get("version")) is not int or row["version"] <= 0
                or type(row.get("verify_jwt")) is not bool
                or "entrypoint_path" not in row):
            raise RuntimeError("exact active function identity/version/configuration is unavailable")
        if "import_map_path" in row:
            import_map = row["import_map_path"]
        elif row.get("import_map") is False:
            import_map = None
        else:
            raise RuntimeError("exact active function import-map configuration is unavailable")
        config = {"verify_jwt": row["verify_jwt"], "entrypoint": function_path(row["entrypoint_path"], name),
                  "import_map": function_path(import_map, name) if import_map else None}
        return {"identity": row["id"], "version": str(row["version"]), "configuration": config}

    def _capture_function(self, name):
        metadata = self._metadata(name)
        if metadata is None: return absent()
        with tempfile.TemporaryDirectory(prefix="active-function-") as directory:
            root = Path(directory)
            self._command(["functions", "download", name, "--use-api"], cwd=root)
            files = encode_files(root / "supabase/functions" / name)
        if self._metadata(name) != metadata: raise RuntimeError("active function changed during capture")
        for key in ("entrypoint", "import_map"):
            path = metadata["configuration"][key]
            if path is not None and path not in files: raise RuntimeError("download omitted configured function bytes")
        return {"exists": True, **metadata, "files": files, "values": {}}

    def _capture_role(self):
        with self._connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute("SELECT to_jsonb(r) AS role FROM pg_catalog.pg_authid r WHERE rolname=%s", (RUNTIME_ROLE,)).fetchone()
            if row is None: return absent()
            role = row["role"]
            memberships = connection.execute("""SELECT parent.rolname AS role, grantor.rolname AS grantor,
                m.admin_option, to_jsonb(m)->'inherit_option' AS inherit_option,
                to_jsonb(m)->'set_option' AS set_option FROM pg_catalog.pg_auth_members m
                JOIN pg_catalog.pg_roles parent ON parent.oid=m.roleid
                JOIN pg_catalog.pg_roles grantor ON grantor.oid=m.grantor
                WHERE m.member=%s ORDER BY parent.rolname,grantor.rolname""", (role["oid"],)).fetchall()
            settings = connection.execute("""SELECT COALESCE(d.datname,'') AS database,s.setconfig
                FROM pg_catalog.pg_db_role_setting s LEFT JOIN pg_catalog.pg_database d ON d.oid=s.setdatabase
                WHERE s.setrole=%s ORDER BY database""", (role["oid"],)).fetchall()
            configuration = {"attributes": {key: role[key] for key in ROLE_ATTRIBUTES},
                             "memberships": [dict(row) for row in memberships],
                             "settings": [dict(row) for row in settings]}
            values = {"password_verifier": role["rolpassword"]}
            return {"exists": True, "identity": RUNTIME_ROLE,
                    "version": hashlib.sha256(canonical([configuration, values])).hexdigest(),
                    "configuration": configuration, "files": {}, "values": values}

    def capture(self, name):
        if not self.original:
            self.captured_at = datetime.now(timezone.utc).isoformat()
        if name in FUNCTIONS: snapshot = self._capture_function(name)
        elif name == "runtime-role": snapshot = self._capture_role()
        elif name == "dashboard-secrets":
            if self.known_secrets is None:
                try: self.known_secrets = json.loads(self.environment.get("DASHBOARD_PRIOR_MANAGED_SECRETS_JSON", "{}"))
                except ValueError as error: raise RuntimeError("protected prior managed secret values are malformed") from error
                if not isinstance(self.known_secrets, dict): raise RuntimeError("protected prior managed secret values are malformed")
            snapshot = capture_managed_secrets(self._secret_inventory(), self.known_secrets)
            literal_secret_values(snapshot["values"])
        else: raise RuntimeError("native component is not allowlisted")
        snapshot = validate_snapshot(name, snapshot)
        self.original.setdefault(name, copy.deepcopy(snapshot))
        self.readbacks[name] = copy.deepcopy(snapshot)
        return snapshot

    def _write_function(self, name, candidate):
        if not candidate["exists"]:
            self._command(["functions", "delete", name, "--yes"])
            return
        config = candidate["configuration"]
        if set(config) != {"verify_jwt", "entrypoint", "import_map"} or type(config["verify_jwt"]) is not bool:
            raise RuntimeError("complete native function configuration is required")
        with tempfile.TemporaryDirectory(prefix="native-function-deploy-") as directory:
            root = Path(directory); source = root / "supabase/functions" / name
            source.mkdir(parents=True)
            for path, encoded in candidate["files"].items():
                target = source / path; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(encoded, validate=True))
            settings = [f'[functions.{json.dumps(name)}]', 'enabled = true',
                f'verify_jwt = {str(config["verify_jwt"]).lower()}']
            for key in ("entrypoint", "import_map"):
                if config[key] is not None:
                    path = function_path(config[key], name)
                    if path not in candidate["files"]: raise RuntimeError("configured native function file is absent")
                    settings.append(f'{key} = {json.dumps("./functions/" + name + "/" + path)}')
            (root / "supabase/config.toml").write_text("\n".join(settings) + "\n")
            self._command(["functions", "deploy", name, "--use-api"], cwd=root)

    def _write_secrets(self, candidate):
        values = candidate["values"]
        literal_secret_values(values)
        if set(values) - set(MANAGED_SECRETS): raise RuntimeError("secret mutation is not allowlisted")
        if any(not isinstance(v, str) or not v or any(c in v for c in '\r\n\x00') for v in values.values()):
            raise RuntimeError("managed secrets must be nonempty single-line values")
        current = self.capture("dashboard-secrets")["values"]
        changed = {key: value for key, value in values.items() if current.get(key) != value}
        if changed:
            with tempfile.TemporaryDirectory(prefix="managed-release-secret-") as directory:
                path = Path(directory) / "secrets.env"
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w") as stream: stream.write("".join(f"{k}={v}\n" for k, v in sorted(changed.items())))
                # Remember both candidates for lost-response readback. Capture
                # below resolves the actual inventory by digest, never by guess.
                try: self._command(["secrets", "set", "--env-file", str(path)])
                finally: self._refresh_known_secrets(current, values)
        removed = sorted(set(current) - set(values))
        if removed:
            try: self._command(["secrets", "unset", *removed, "--yes"])
            finally: self._refresh_known_secrets(current, values)

    def _refresh_known_secrets(self, prior, candidate):
        rows = self._secret_inventory()
        known = {}
        for row in rows:
            key = row.get("name")
            if key not in MANAGED_SECRETS: continue
            matches = [value for value in (prior.get(key), candidate.get(key)) if isinstance(value, str)
                       and hashlib.sha256(value.encode()).hexdigest() == row.get("digest")]
            if not matches: raise RuntimeError("managed secret value/digest cannot be recovered")
            known[key] = matches[0]
        self.known_secrets = known

    def _write_role(self, candidate):
        # Transactional PostgreSQL DDL: a failure before commit leaves role
        # state unchanged, and the shared engine proves that before recovery.
        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s", (RUNTIME_ROLE,)).fetchone()
            if not candidate["exists"]:
                if exists: connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(RUNTIME_ROLE)))
                return
            if not exists: connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(RUNTIME_ROLE)))
            attrs = candidate["configuration"]["attributes"]
            if set(attrs) != set(ROLE_ATTRIBUTES): raise RuntimeError("complete runtime role attributes are required")
            options = []
            for key, word in (("rolsuper", "SUPERUSER"), ("rolinherit", "INHERIT"), ("rolcreaterole", "CREATEROLE"),
                ("rolcreatedb", "CREATEDB"), ("rolcanlogin", "LOGIN"), ("rolreplication", "REPLICATION"), ("rolbypassrls", "BYPASSRLS")):
                if type(attrs[key]) is not bool: raise RuntimeError("runtime role attribute is malformed")
                options.append(sql.SQL(word if attrs[key] else "NO" + word))
            options.extend([sql.SQL("CONNECTION LIMIT {}").format(sql.Literal(attrs["rolconnlimit"])),
                sql.SQL("PASSWORD {}").format(sql.Literal(candidate["values"]["password_verifier"]))])
            if attrs["rolvaliduntil"] is not None:
                options.append(sql.SQL("VALID UNTIL {}").format(sql.Literal(attrs["rolvaliduntil"])))
            connection.execute(sql.SQL("ALTER ROLE {} WITH {}").format(sql.Identifier(RUNTIME_ROLE), sql.SQL(" ").join(options)))
            memberships = connection.execute("""SELECT p.rolname AS role,g.rolname AS grantor FROM pg_catalog.pg_auth_members m
                JOIN pg_catalog.pg_roles p ON p.oid=m.roleid JOIN pg_catalog.pg_roles g ON g.oid=m.grantor
                JOIN pg_catalog.pg_roles r ON r.oid=m.member WHERE r.rolname=%s""", (RUNTIME_ROLE,)).fetchall()
            for membership in memberships:
                connection.execute(sql.SQL("REVOKE {} FROM {} GRANTED BY {}").format(
                    sql.Identifier(membership["role"]), sql.Identifier(RUNTIME_ROLE), sql.Identifier(membership["grantor"])))
            for membership in candidate["configuration"]["memberships"]:
                statement = sql.SQL("GRANT {} TO {} WITH ADMIN {}").format(sql.Identifier(membership["role"]),
                    sql.Identifier(RUNTIME_ROLE), sql.SQL("TRUE" if membership["admin_option"] else "FALSE"))
                for field, word in (("inherit_option", "INHERIT"), ("set_option", "SET")):
                    if membership[field] is not None:
                        statement += sql.SQL(", {} {}").format(sql.SQL(word), sql.SQL("TRUE" if membership[field] else "FALSE"))
                statement += sql.SQL(" GRANTED BY {}").format(sql.Identifier(membership["grantor"]))
                connection.execute(statement)
            settings = connection.execute("""SELECT COALESCE(d.datname,'') AS database FROM pg_catalog.pg_db_role_setting s
                JOIN pg_catalog.pg_roles r ON r.oid=s.setrole LEFT JOIN pg_catalog.pg_database d ON d.oid=s.setdatabase
                WHERE r.rolname=%s""", (RUNTIME_ROLE,)).fetchall()
            for setting in settings:
                statement = sql.SQL("ALTER ROLE {}").format(sql.Identifier(RUNTIME_ROLE))
                if setting["database"]: statement += sql.SQL(" IN DATABASE {}").format(sql.Identifier(setting["database"]))
                connection.execute(statement + sql.SQL(" RESET ALL"))
            for setting in candidate["configuration"]["settings"]:
                for pair in setting["setconfig"]:
                    key, value = pair.split("=", 1)
                    statement = sql.SQL("ALTER ROLE {}").format(sql.Identifier(RUNTIME_ROLE))
                    if setting["database"]: statement += sql.SQL(" IN DATABASE {}").format(sql.Identifier(setting["database"]))
                    # FROM CURRENT preserves list-valued GUC syntax (including
                    # quoted search_path identifiers) without SQL interpolation.
                    connection.execute("SELECT pg_catalog.set_config(%s,%s,true)", (key, value))
                    connection.execute(statement + sql.SQL(" SET {} FROM CURRENT").format(sql.Identifier(key)))

    def apply(self, name, candidate):
        candidate = validate_snapshot(name, candidate, candidate=True)
        if name in FUNCTIONS: self._write_function(name, candidate)
        elif name == "dashboard-secrets": self._write_secrets(candidate)
        elif name == "runtime-role": self._write_role(candidate)
        else: raise RuntimeError("native component mutation is not allowlisted")
        return self.capture(name)

    def restore(self, name, prior):
        # Recovery does not depend on a still-current protected secret copy:
        # retained encrypted prior values plus attempted candidate values are
        # installed by hydrate_recovery before any independent recovery read.
        restored = self.apply(name, prior)
        if name in FUNCTIONS and prior["exists"]: return restored
        if restored != prior: raise RuntimeError("native restoration differs from exact prior state")
        return None

    def hydrate_recovery(self, name, prior, candidate):
        if name == "dashboard-secrets":
            self._refresh_known_secrets(prior["values"], (candidate or {}).get("values", {}))

    def attest_recovery(self, name, prior, candidate, current):
        """Prove release-owned state; a changed flag alone is not authority.

        Atomic writes require full candidate content. Supabase first installs
        allocate version 1; upgrades preserve the function ID and advance once.
        Secrets can partially set/unset, but every value AND presence must be
        from the encrypted prior/candidate pair, with canonical digest metadata.
        No Site proof is invented here; its reviewed transport owns that proof.
        """
        prior = validate_snapshot(name, prior)
        candidate = validate_snapshot(name, candidate, candidate=True)
        current = validate_snapshot(name, current)
        if name == "dashboard-secrets":
            missing = object()
            if any(set(value["values"]) - set(MANAGED_SECRETS) for value in (prior, candidate, current)):
                return False
            for key in MANAGED_SECRETS:
                value = current["values"].get(key, missing)
                if value not in (prior["values"].get(key, missing), candidate["values"].get(key, missing)):
                    return False
            inventory = [{"name": key, "digest": hashlib.sha256(value.encode()).hexdigest()}
                         for key, value in current["values"].items() if isinstance(value, str)]
            expected = capture_managed_secrets(inventory, current["values"])
            return current == expected
        if name not in (*FUNCTIONS, "runtime-role") or not same_content(candidate, current):
            return False
        if not candidate["exists"]:
            return current == candidate
        if name == "runtime-role":
            version = hashlib.sha256(canonical([candidate["configuration"], candidate["values"]])).hexdigest()
            return (current["identity"] == RUNTIME_ROLE and candidate["identity"] in (None, RUNTIME_ROLE)
                    and (not prior["exists"] or prior["identity"] == RUNTIME_ROLE)
                    and current["version"] == version and candidate["version"] in (None, version))
        if not re.fullmatch(r"[1-9][0-9]*", str(current["version"])):
            return False
        if candidate["identity"] is not None and current["identity"] != candidate["identity"]:
            return False
        if candidate["version"] is not None and current["version"] != candidate["version"]:
            return False
        if prior["exists"]:
            return (current["identity"] == prior["identity"]
                    and re.fullmatch(r"[1-9][0-9]*", str(prior["version"])) is not None
                    and int(current["version"]) == int(prior["version"]) + 1)
        return current["version"] == "1"

    def plan(self, context):
        from scripts.verify_personal_stock_agent_v1 import git_files
        password = secrets.token_urlsafe(36)
        with self._connection() as connection:
            grantor = connection.execute("SELECT current_user AS name").fetchone()["name"]
        previous_role = validate_snapshot("runtime-role", self._capture_role())
        role = {"exists": True, "identity": RUNTIME_ROLE, "version": None, "files": {},
            "values": {"password_verifier": scram_verifier(password)}, "configuration": {
                "attributes": {"rolsuper": False, "rolinherit": True, "rolcreaterole": False, "rolcreatedb": False,
                    "rolcanlogin": True, "rolreplication": False, "rolbypassrls": False, "rolconnlimit": -1,
                    "rolvaliduntil": previous_role["configuration"]["attributes"]["rolvaliduntil"] if previous_role["exists"] else None},
                "memberships": [{"role": PRIVILEGE_ROLE, "grantor": grantor, "admin_option": False,
                                  "inherit_option": True, "set_option": True}],
                "settings": [{"database": "", "setconfig": ["search_path=pg_catalog, public"]}]}}
        values = {"DASHBOARD_DATABASE_URL": runtime_url(self.environment["SUPAVISOR_SESSION_URL"], RUNTIME_ROLE, password),
                  "DASHBOARD_ALLOWED_ORIGINS": context["allowed_origin"], "DASHBOARD_OWNER_USER_ID": context["owner_user_id"]}
        secret = capture_managed_secrets([{"name": k, "digest": hashlib.sha256(v.encode()).hexdigest()} for k, v in values.items()], values)
        candidates = {"runtime-role": role, "dashboard-secrets": secret}
        config = tomllib.loads((self.root / "supabase/config.toml").read_text())["functions"]
        for name in FUNCTIONS:
            cfg = config[name]
            candidates[name] = {"exists": True, "identity": None, "version": None, "values": {},
                "files": {path: base64.b64encode(raw).decode() for path, raw in git_files(self.root, context["candidate_sha"], f"supabase/functions/{name}").items()},
                "configuration": {"verify_jwt": cfg["verify_jwt"], "entrypoint": function_path(cfg["entrypoint"], name),
                    "import_map": function_path(cfg["import_map"], name) if cfg.get("import_map") else None}}
        if tuple(candidates) != BACKEND_COMPONENTS:
            raise RuntimeError("complete protected backend candidate is required")
        return candidates

    def retain(self, encrypted):
        """Append-only ciphertext retention, committed before component writes.

        This uses the same protected DB administrator and unresolved lease as
        the established release metadata primitives. It is not a public table.
        """
        from cryptography.fernet import Fernet
        state = json.loads(Fernet(self.environment["RELEASE_RECOVERY_KEY"].encode()).decrypt(encrypted))
        context = state["release_context"]
        if context["project_ref"] != self._project() or context["candidate_sha"] != self.context["candidate_sha"]:
            raise RuntimeError("encrypted journal project/candidate binding mismatch")
        run_id = str(context["release_run_id"])
        run_attempt = str(context["release_run_attempt"])
        if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", run_attempt):
            raise RuntimeError("protected journal run identity and attempt are required")
        if (run_id != str(self.context.get("release_run_id"))
                or run_attempt != str(self.context.get("release_run_attempt"))):
            raise RuntimeError("encrypted journal run identity/attempt binding mismatch")
        with self._connection() as connection:
            lease = connection.execute("SELECT owner,state FROM public.stock_agent_release_mutation_lease WHERE singleton FOR SHARE").fetchone()
            if not lease or lease["state"] != "recovery_required" or lease["owner"] != self.context.get("lease_owner"):
                raise RuntimeError("encrypted journal retention requires the held protected lease")
            connection.execute(f"CREATE TABLE IF NOT EXISTS {JOURNALS} (sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, project_ref text NOT NULL, candidate_sha text NOT NULL, run_id text NOT NULL, run_attempt text NOT NULL, ciphertext bytea NOT NULL, captured_at timestamptz NOT NULL DEFAULT clock_timestamp())")
            connection.execute(f"ALTER TABLE {JOURNALS} ADD COLUMN IF NOT EXISTS run_attempt text")
            connection.execute(f"REVOKE ALL ON {JOURNALS} FROM PUBLIC")
            # Supabase default privileges can grant directly to these roles;
            # revoking PUBLIC alone does not remove those direct grants.
            for role in ("anon", "authenticated", "service_role"):
                if connection.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s", (role,)).fetchone():
                    connection.execute(sql.SQL("REVOKE ALL ON {}.{} FROM {}").format(
                        sql.Identifier("public"), sql.Identifier(JOURNALS.split(".")[1]), sql.Identifier(role)))
            connection.execute(f"ALTER TABLE {JOURNALS} ENABLE ROW LEVEL SECURITY")
            row = connection.execute(f"INSERT INTO {JOURNALS}(project_ref,candidate_sha,run_id,run_attempt,ciphertext) VALUES(%s,%s,%s,%s,%s) RETURNING sequence,captured_at",
                               (self._project(), self.context["candidate_sha"], run_id, run_attempt, encrypted)).fetchone()
            self.last_journal = {"sequence": int(row["sequence"]), "run_id": int(run_id),
                "run_attempt": int(run_attempt), "captured_at": row["captured_at"].isoformat(),
                "ciphertext_sha256": hashlib.sha256(encrypted).hexdigest()}

    def recover_retained(self, run_id, run_attempt):
        run_id, run_attempt = str(run_id), str(run_attempt)
        if (not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", run_attempt)
                or run_id != str(self.context.get("release_run_id"))
                or run_attempt != str(self.context.get("release_run_attempt"))):
            raise RuntimeError("protected retained journal run identity/attempt mismatch")
        with self._connection() as connection:
            row = connection.execute(f"SELECT ciphertext FROM {JOURNALS} WHERE project_ref=%s AND candidate_sha=%s AND run_id=%s AND run_attempt=%s ORDER BY sequence DESC LIMIT 1",
                (self._project(), self.context["candidate_sha"], run_id, run_attempt)).fetchone()
        if not row: raise RuntimeError("retained encrypted component journal is unavailable")
        encrypted = bytes(row["ciphertext"])
        from cryptography.fernet import Fernet
        try:
            context = json.loads(Fernet(self.environment["RELEASE_RECOVERY_KEY"].encode()).decrypt(encrypted))["release_context"]
        except Exception as error:
            raise RuntimeError("retained encrypted component journal identity is invalid") from error
        if (context.get("project_ref") != self._project() or context.get("candidate_sha") != self.context["candidate_sha"]
                or str(context.get("release_run_id")) != run_id
                or str(context.get("release_run_attempt")) != run_attempt):
            raise RuntimeError("retained encrypted component journal identity mismatch")
        return encrypted

    @staticmethod
    def _decoded_files(snapshot):
        return {path: base64.b64decode(raw, validate=True) for path, raw in snapshot["files"].items()}

    @staticmethod
    def _tree_sha256(files):
        digest = hashlib.sha256()
        for path, raw in sorted(files.items()):
            digest.update(path.encode() + b"\0" + raw + b"\0")
        return digest.hexdigest()

    def receipt(self, candidate_sha):
        from scripts.verify_personal_stock_agent_v1 import git_files
        if (candidate_sha != self.context.get("candidate_sha")
                or not isinstance(self.captured_at, str)):
            raise RuntimeError("protected backend receipt candidate is incomplete")
        evidence_directory = self.context.get("evidence_directory")
        if not isinstance(evidence_directory, str) or not evidence_directory:
            raise RuntimeError("protected backend evidence directory is unavailable or unsafe")
        static_receipt_path = self.context.get("static_build_receipt")
        if not isinstance(static_receipt_path, str):
            raise RuntimeError("candidate static build receipt is unavailable")
        try:
            static_receipt = json.loads(Path(static_receipt_path).read_text())
        except (OSError, ValueError) as error:
            raise RuntimeError("candidate static build receipt is unavailable") from error
        static_root = self.root / "dist"
        static_bytes = {path.relative_to(static_root).as_posix(): path.read_bytes()
                        for path in sorted(static_root.rglob("*")) if path.is_file() and not path.is_symlink()}
        static_files = {path: hashlib.sha256(raw).hexdigest() for path, raw in static_bytes.items()}
        if (not isinstance(static_receipt, dict) or static_receipt.get("status") != "verified"
                or static_receipt.get("candidate_sha") != candidate_sha
                or static_receipt.get("files") != static_files or not static_files
                or static_receipt.get("build_sha256") != self._tree_sha256(static_bytes)
                or any(path.is_symlink() for path in static_root.rglob("*"))):
            raise RuntimeError("candidate static build receipt differs from exact build bytes")
        static_receipt["source_sha256"] = self._tree_sha256(
            git_files(self.root, candidate_sha, "apps/web")
        )
        self.static_receipt = static_receipt
        evidence_parent = Path(evidence_directory)
        evidence_root = evidence_parent / "backend-component-evidence"
        if (not evidence_parent.is_absolute() or not evidence_parent.is_dir()
                or evidence_parent.is_symlink() or evidence_root.exists()):
            raise RuntimeError("protected backend evidence directory is unavailable or reused")
        evidence_root.mkdir(mode=0o700)
        functions = []
        readbacks = []
        manifest_components = []
        for name in FUNCTIONS:
            current = validate_snapshot(name, self.readbacks.get(name) or self.capture(name))
            expected = git_files(self.root, candidate_sha, f"supabase/functions/{name}")
            files = self._decoded_files(current)
            if files != expected:
                raise RuntimeError("protected backend readback differs from candidate bytes")
            deployed_hash = self._tree_sha256(files)
            deployed_prefix = f"deployed/{name}"
            for path, raw in files.items():
                target = evidence_root / deployed_prefix / path
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.write_bytes(raw)
                target.chmod(0o600)
            prior = validate_snapshot(name, self.original[name])
            prior_row = {"exists": prior["exists"], "deployment_id": prior["identity"],
                "version": prior["version"], "configuration": prior["configuration"],
                "captured_at": self.captured_at, "artifact_prefix": None, "source_sha256": None}
            if prior["exists"]:
                prior_files = self._decoded_files(prior)
                prior_prefix = f"prior/{name}"
                for path, raw in prior_files.items():
                    target = evidence_root / prior_prefix / path
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target.write_bytes(raw)
                    target.chmod(0o600)
                prior_row.update(artifact_prefix=prior_prefix, source_sha256=self._tree_sha256(prior_files))
            functions.append({"function": name, "deployment_id": current["identity"], "git_sha": candidate_sha,
                "function_version": int(current["version"]), "source_sha256": deployed_hash})
            readbacks.append({"component": name, "candidate_sha": candidate_sha,
                "deployment_id": current["identity"], "version": current["version"],
                "configuration": current["configuration"], "origin": "management_plane_download",
                "artifact_prefix": deployed_prefix, "deployed_sha256": deployed_hash, "prior": prior_row})
            manifest_components.append({"component": name, "deployed_prefix": deployed_prefix,
                "deployed_sha256": deployed_hash, "prior_prefix": prior_row["artifact_prefix"],
                "prior_sha256": prior_row["source_sha256"]})
        manifest = {"format": "stocks-protected-backend-evidence-v1", "candidate_sha": candidate_sha,
            "project_ref": self._project(), "release_run_id": int(self.context["release_run_id"]),
            "release_run_attempt": int(self.context["release_run_attempt"]), "components": manifest_components}
        manifest_raw = canonical(manifest)
        (evidence_root / "manifest.json").write_bytes(manifest_raw)
        (evidence_root / "manifest.json").chmod(0o600)
        return {"candidate_sha": candidate_sha, "functions": functions,
            "static_assets": copy.deepcopy(self.static_receipt),
            "component_readbacks": readbacks,
            "backend_evidence": {"directory": str(evidence_root),
                "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest()}}

    def journal_receipt(self):
        if not isinstance(self.last_journal, dict):
            raise RuntimeError("protected recovery journal receipt is unavailable")
        return copy.deepcopy(self.last_journal)

    def finalize_backend_evidence(self, receipt, recovery):
        root = Path(receipt["backend_evidence"]["directory"])
        if not root.is_dir() or set(recovery) != {
            "sequence", "run_id", "run_attempt", "captured_at", "ciphertext_sha256"
        }:
            raise RuntimeError("protected backend recovery evidence is malformed")
        raw = canonical(recovery)
        target = root / "recovery-metadata.json"
        target.write_bytes(raw)
        target.chmod(0o600)
        receipt["backend_evidence"]["recovery_metadata_sha256"] = hashlib.sha256(raw).hexdigest()

    def artifact(self, artifact_id):
        from scripts.protected_evidence import GitHubProductionDataSource
        source = GitHubProductionDataSource(self.environment["GITHUB_REPOSITORY"], self._project(), None)
        source.candidate = self.context["candidate_sha"]
        return source.artifact(artifact_id, active_run_id=int(self.context["release_run_id"]))


def create_adapter(context):
    return NativeReleaseAdapter(context)
