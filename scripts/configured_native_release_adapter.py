"""Repository-native PostgreSQL/Supabase release and recovery transport.

Construction performs no I/O. The production loader requires the manifest-bound
Sites transport before any database or Supabase command. No CI Sites transport
is configured: `site` deliberately remains None. There is no executable/env/API
escape hatch for that missing capability.

Snapshots contain base64 file bytes and recoverable credentials. They belong in
the authenticated encrypted journal, never stdout or a public release receipt.
Supabase restore redeploys prior bytes/configuration and allocates a NEW version;
the engine retains both captured and restoration identities.
"""
from __future__ import annotations

import base64
import copy
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

from scripts.release_components import (FUNCTIONS, MANAGED_SECRETS, ROOT, canonical,
    capture_managed_secrets, require_site_transport, validate_snapshot)
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
    for prefix in (f"supabase/functions/{name}/", f"functions/{name}/"):
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
    site = None

    def __init__(self, context, *, runner=None, connector=None, environment=None, repo_root=ROOT):
        self.context = dict(context)
        self.runner = runner or subprocess.run
        self.connector = connector or psycopg.connect
        self.environment = dict(os.environ if environment is None else environment)
        self.root = Path(repo_root)
        self.known_secrets = None  # parse only after the Sites gate
        self.original = {}
        self.readbacks = {}

    def _project(self):
        project = self.context.get("project_ref", "")
        if not re.fullmatch(r"[a-z0-9]{20}", project): raise RuntimeError("exact native project reference is required")
        return project

    def _command(self, args, *, cwd=None, json_output=False):
        result = self.runner([*CLI, *args, "--project-ref", self._project()],
            cwd=cwd or self.root, capture_output=True, text=True, check=False,
            env={**os.environ, **self.environment})
        if result.returncode != 0:
            # CLI stderr can contain credentials, function bytes, or signed URLs.
            raise RuntimeError("protected Supabase command failed: " + " ".join(args[:2]))
        if not json_output: return None
        try: return json.loads(result.stdout)
        except (ValueError, TypeError) as error: raise RuntimeError("Supabase inventory is malformed") from error

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
                or "entrypoint_path" not in row or "import_map_path" not in row):
            raise RuntimeError("exact active function identity/version/configuration is unavailable")
        config = {"verify_jwt": row["verify_jwt"], "entrypoint": function_path(row["entrypoint_path"], name),
                  "import_map": function_path(row["import_map_path"], name) if row["import_map_path"] else None}
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
        if name in FUNCTIONS: snapshot = self._capture_function(name)
        elif name == "runtime-role": snapshot = self._capture_role()
        elif name == "dashboard-secrets":
            if self.known_secrets is None:
                try: self.known_secrets = json.loads(self.environment.get("DASHBOARD_PRIOR_MANAGED_SECRETS_JSON", "{}"))
                except ValueError as error: raise RuntimeError("protected prior managed secret values are malformed") from error
                if not isinstance(self.known_secrets, dict): raise RuntimeError("protected prior managed secret values are malformed")
            snapshot = capture_managed_secrets(self._command(["secrets", "list", "--output", "json"], json_output=True), self.known_secrets)
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
        rows = self._command(["secrets", "list", "--output", "json"], json_output=True)
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

    def plan(self, context):
        require_site_transport(self.root, adapter=self.site)
        from scripts.build_owner_dashboard_static import build_static_release
        from scripts.verify_personal_stock_agent_v1 import git_files
        password = secrets.token_urlsafe(36)
        with self._connection() as connection:
            grantor = connection.execute("SELECT current_user AS name").fetchone()["name"]
        previous_role = self.capture("runtime-role")
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
        self.static_receipt = build_static_release(context["project_ref"], context["site_origin"], repo_root=self.root, runner=self.runner)
        candidates["owner-web-site"] = self.site.plan(context)
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
        if not re.fullmatch(r"[1-9][0-9]*", run_id): raise RuntimeError("protected journal run identity is required")
        with self._connection() as connection:
            lease = connection.execute("SELECT owner,state FROM public.stock_agent_release_mutation_lease WHERE singleton FOR SHARE").fetchone()
            if not lease or lease["state"] != "recovery_required" or lease["owner"] != self.context.get("lease_owner"):
                raise RuntimeError("encrypted journal retention requires the held protected lease")
            connection.execute(f"CREATE TABLE IF NOT EXISTS {JOURNALS} (sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, project_ref text NOT NULL, candidate_sha text NOT NULL, run_id text NOT NULL, ciphertext bytea NOT NULL, captured_at timestamptz NOT NULL DEFAULT clock_timestamp())")
            connection.execute(f"REVOKE ALL ON {JOURNALS} FROM PUBLIC")
            # Supabase default privileges can grant directly to these roles;
            # revoking PUBLIC alone does not remove those direct grants.
            for role in ("anon", "authenticated", "service_role"):
                if connection.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s", (role,)).fetchone():
                    connection.execute(sql.SQL("REVOKE ALL ON {}.{} FROM {}").format(
                        sql.Identifier("public"), sql.Identifier(JOURNALS.split(".")[1]), sql.Identifier(role)))
            connection.execute(f"ALTER TABLE {JOURNALS} ENABLE ROW LEVEL SECURITY")
            connection.execute(f"INSERT INTO {JOURNALS}(project_ref,candidate_sha,run_id,ciphertext) VALUES(%s,%s,%s,%s)",
                               (self._project(), self.context["candidate_sha"], run_id, encrypted))

    def recover_retained(self, run_id):
        with self._connection() as connection:
            row = connection.execute(f"SELECT ciphertext FROM {JOURNALS} WHERE project_ref=%s AND candidate_sha=%s AND run_id=%s ORDER BY sequence DESC LIMIT 1",
                (self._project(), self.context["candidate_sha"], str(run_id))).fetchone()
        if not row: raise RuntimeError("retained encrypted component journal is unavailable")
        return bytes(row["ciphertext"])

    def receipt(self, candidate_sha):
        # Publication requires real immutable artifact IDs, including the Site's
        # management-plane downloads. That final integration belongs to the
        # absent reviewed Sites transport; never manufacture artifact receipts.
        require_site_transport(self.root, adapter=self.site)
        return self.site.release_receipt(candidate_sha, copy.deepcopy(self.original), copy.deepcopy(self.readbacks), self.static_receipt)

    def artifact(self, artifact_id):
        from scripts.protected_evidence import GitHubProductionDataSource
        source = GitHubProductionDataSource(self.environment["GITHUB_REPOSITORY"], self._project(), None)
        source.candidate = self.context["candidate_sha"]
        return source.artifact(artifact_id, active_run_id=int(self.context["release_run_id"]))


def create_adapter(context):
    return NativeReleaseAdapter(context)
