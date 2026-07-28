"""MongoDB persistence with an explicitly enabled in-memory demo mode."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import os
import threading
import uuid

from werkzeug.security import generate_password_hash

from services.runtime_config import is_production_environment, memory_fallback_allowed, ProductionConfigurationError

try:
    from bson import ObjectId
    from pymongo import ASCENDING, DESCENDING, MongoClient
    from pymongo.errors import DuplicateKeyError
    from pymongo.uri_parser import parse_uri
except ImportError:  # pragma: no cover - exercised on minimal installations
    ObjectId = None
    MongoClient = None
    parse_uri = None
    ASCENDING, DESCENDING = 1, -1
    class DuplicateKeyError(Exception):
        pass


def utcnow():
    return datetime.now(timezone.utc)


def strip_mongo_id(payload):
    if isinstance(payload, dict):
        payload = payload.copy()
        payload.pop("_id", None)
    return payload


class MongoRepository:
    collection_names = (
        "users",
        "market_records",
        "search_records",
        "product_results",
        "search_results",
        "search_cache",
        "ai_insights",
        "ai_search_logs",
        "analytics_reports",
        "evidence_records",
        "research_records",
        "comparison_groups",
        "watchlist_items",
        "monitor_refresh_claims",
        "price_snapshots",
        "price_alert_events",
        "prediction_records",
        "audit_logs",
        "activity_logs",
        "password_reset_tokens",
        "testing_records",
    )

    def __init__(self, uri=None, database_name=None, allow_memory_fallback=None):
        self.uri = os.getenv("MONGO_URI") if uri is None else uri
        self.database_name = (
            database_name
            or os.getenv("MONGO_DATABASE")
            or "precision_curator_production"
        )
        if is_production_environment() and allow_memory_fallback:
            raise ProductionConfigurationError("Memory fallback cannot be enabled in production.")
        demo_mode = memory_fallback_allowed()
        if allow_memory_fallback and not demo_mode:
            raise ProductionConfigurationError("In-memory storage requires DEMO_MODE=true.")
        self.allow_memory_fallback = demo_mode if allow_memory_fallback is None else bool(allow_memory_fallback)
        self.client = None
        self.db = None
        self.error = None
        self._memory = {name: [] for name in self.collection_names}
        self._monitor_refresh_lock = threading.Lock()
        self._price_alert_lock = threading.Lock()
        if self.uri and MongoClient:
            try:
                self.client = MongoClient(self.uri, serverSelectionTimeoutMS=5000)
                self.client.admin.command("ping")
                self.db = self.client[self.database_name]
                self._create_indexes()
                parsed = parse_uri(self.uri) if parse_uri else {}
                nodes = parsed.get("nodelist") or []
                host = nodes[0][0] if nodes else "unknown"
                print(f"MongoDB connected: host={host}, database={self.database_name}", flush=True)
            except Exception as exc:  # Never leak a URI through an error string.
                self.client = None
                self.db = None
                self.error = f"MongoDB unavailable ({exc.__class__.__name__})"
                if not self.allow_memory_fallback:
                    raise RuntimeError("MongoDB is unavailable and memory fallback is disabled.") from exc
        elif not self.uri:
            self.error = "MONGO_URI is not configured"
            if not self.allow_memory_fallback:
                raise RuntimeError("MONGO_URI is required unless DEMO_MODE=true.")
        else:
            self.error = "PyMongo is not installed"
            if not self.allow_memory_fallback:
                raise RuntimeError("MongoDB support is unavailable and memory fallback is disabled.")

    @property
    def mode(self):
        return "mongodb" if self.db is not None else "memory_fallback"

    def status(self):
        if self.db is not None:
            try:
                self.client.admin.command("ping")
                return {
                    "name": "MongoDB",
                    "status": "connected",
                    "detail": self.database_name,
                }
            except Exception:
                return {
                    "name": "MongoDB",
                    "status": "unavailable",
                    "detail": "MongoDB ping failed.",
                }
        return {
            "name": "MongoDB",
            "status": "fallback mode",
            "detail": "Using in-memory fallback; data may not persist after restart.",
        }

    def _create_indexes(self):
        self.db.users.create_index("username", unique=True)
        self.db.users.create_index("email_lower", unique=True, sparse=True)
        self.db.market_records.create_index([("query", ASCENDING), ("normalized_price", ASCENDING)])
        self.db.market_records.create_index([("platform", ASCENDING), ("category", ASCENDING)])
        self.db.search_records.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        self.db.search_records.create_index("search_run_id", unique=True, sparse=True)
        self.db.product_results.create_index("search_record_id")
        self.db.search_results.create_index([("search_record_id", ASCENDING), ("created_at", DESCENDING)])
        self.db.search_cache.create_index("cache_key", unique=True)
        self.db.search_cache.create_index("expires_at")
        self.db.ai_search_logs.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        self.db.ai_insights.create_index([("user_id", ASCENDING), ("search_run_id", ASCENDING), ("generated_at", DESCENDING)])
        self.db.analytics_reports.create_index("search_record_id")
        self.db.analytics_reports.create_index(
            [("user_id", ASCENDING), ("analysis_signature", ASCENDING)],
            unique=True,
            partialFilterExpression={"document_type": "analysis_record", "analysis_signature": {"$type": "string"}},
        )
        self.db.evidence_records.create_index([("user_id", ASCENDING), ("saved_at", DESCENDING)])
        self.db.research_records.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        self.db.comparison_groups.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        self.db.watchlist_items.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
        self.db.watchlist_items.create_index([("user_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)])
        self.db.watchlist_items.create_index(
            [("user_id", ASCENDING), ("auto_refresh_enabled", ASCENDING)],
            unique=True,
            partialFilterExpression={"status": "active", "auto_refresh_enabled": True},
            name="one_active_auto_monitor_per_owner",
        )
        self.db.monitor_refresh_claims.create_index(
            [("monitor_id", ASCENDING), ("scheduled_date", ASCENDING), ("trigger", ASCENDING)],
            unique=True,
            name="unique_scheduled_monitor_execution",
        )
        self.db.price_snapshots.create_index([("watchlist_id", ASCENDING), ("collected_at", DESCENDING)])
        self.db.price_alert_events.create_index(
            [("monitor_id", ASCENDING), ("snapshot_id", ASCENDING), ("alert_rule_version", ASCENDING)],
            unique=True,
            name="unique_price_alert_per_snapshot_rule",
        )
        self.db.price_alert_events.create_index([("owner_user_id", ASCENDING), ("monitor_id", ASCENDING), ("triggered_at", DESCENDING)])
        self.db.prediction_records.create_index([("user_id", ASCENDING), ("watchlist_id", ASCENDING), ("prediction_created_at", DESCENDING)])
        self.db.audit_logs.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        self.db.activity_logs.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        self.db.password_reset_tokens.create_index("token_hash", unique=True)
        self.db.password_reset_tokens.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        self.db.password_reset_tokens.create_index([("user_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)])
        self.db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)

    def _id(self, value):
        if self.db is not None and ObjectId:
            try:
                return ObjectId(str(value))
            except Exception:
                return value
        return str(value)

    @staticmethod
    def _public(document):
        if not document:
            return None
        result = deepcopy(document)
        result["_id"] = str(result.get("_id", ""))
        for key in ("user_id", "owner_user_id", "search_record_id", "watchlist_id", "monitor_id", "snapshot_id", "previous_snapshot_id"):
            if key in result:
                result[key] = str(result[key])
        return result

    @staticmethod
    def _normalize_roles_document(document):
        if not document:
            return document
        role = document.get("role")
        roles = document.get("roles")
        active_role = document.get("active_role")
        if isinstance(roles, (list, tuple)) and roles:
            normalized_roles = [str(value) for value in roles if value]
        elif role:
            normalized_roles = [str(role)]
        else:
            normalized_roles = ["consumer"]
        if active_role not in normalized_roles:
            active_role = role if role in normalized_roles else normalized_roles[0]
        primary_role = document.get("primary_role") or (role if role in normalized_roles else normalized_roles[0])
        if primary_role not in normalized_roles:
            primary_role = normalized_roles[0]
        document["primary_role"] = primary_role
        document["roles"] = normalized_roles
        document["active_role"] = active_role
        document["role"] = active_role
        document["plan"] = MongoRepository._normalize_membership_tier(document.get("plan"))
        document["membership_tier"] = MongoRepository._normalize_membership_tier(document.get("membership_tier") or document["plan"])
        return document

    def _insert(self, collection, document):
        document = deepcopy(document)
        if self.db is not None:
            return str(self.db[collection].insert_one(document).inserted_id)
        document["_id"] = uuid.uuid4().hex
        self._memory[collection].append(document)
        return document["_id"]

    def _find(self, collection, query=None, limit=0, descending=True):
        query = query or {}
        if collection in {"search_records", "evidence_records", "research_records", "testing_records"} and "is_deleted" not in query:
            query = {**query, "is_deleted": {"$ne": True}}
        if collection in {"audit_logs", "activity_logs"} and "is_archived" not in query:
            query = {**query, "is_archived": {"$ne": True}}
        if self.db is not None:
            cursor = self.db[collection].find(query).sort("created_at", DESCENDING if descending else ASCENDING)
            if limit:
                cursor = cursor.limit(limit)
            return [self._public(item) for item in cursor]
        def matches(row, key, value):
            current = row.get(key)
            if isinstance(value, dict):
                if "$ne" in value:
                    return current != value["$ne"]
                if "$nin" in value:
                    return current not in value["$nin"]
                return False
            return str(current) == str(value)
        rows = [row for row in self._memory[collection] if all(matches(row, k, v) for k, v in query.items())]
        rows.sort(key=lambda row: row.get("created_at", datetime.min.replace(tzinfo=timezone.utc)), reverse=descending)
        return [self._public(row) for row in (rows[:limit] if limit else rows)]

    def _soft_update(self, collection, record_id, updates):
        updates = deepcopy(updates)
        updates["updated_at"] = utcnow()
        if self.db is not None:
            self.db[collection].update_one({"_id": self._id(record_id)}, {"$set": updates})
        else:
            for row in self._memory[collection]:
                if str(row.get("_id")) == str(record_id):
                    row.update(updates)
                    break
        return updates

    def ensure_user(self, username, role, email=None):
        username = username.strip()[:80]
        email_lower = self._normalize_email(email)
        if self.db is not None:
            existing = self.db.users.find_one({"username": username})
            if existing:
                existing_roles = existing.get("roles") or ([existing.get("role")] if existing.get("role") else [role])
                updates = {"roles": existing_roles, "primary_role": existing.get("primary_role") or (existing_roles[0] if existing_roles else role), "active_role": role if role in existing_roles else (existing.get("active_role") or existing_roles[0]), "role": role, "updated_at": utcnow(), "display_name": username, "username": username, "plan": self._normalize_membership_tier(existing.get("plan")), "membership_tier": self._normalize_membership_tier(existing.get("membership_tier") or existing.get("plan"))}
                if email and not existing.get("email"):
                    updates["email"] = email
                    updates["email_lower"] = email_lower
                self.db.users.update_one({"_id": existing["_id"]}, {"$set": updates})
                return self._public({**existing, **updates})
        else:
            for row in self._memory["users"]:
                if row["username"] == username:
                    row["roles"] = row.get("roles") or ([row.get("role")] if row.get("role") else [role])
                    row["primary_role"] = row.get("primary_role") or row["roles"][0]
                    if role not in row["roles"]:
                        row["roles"].append(role)
                    row["active_role"] = role
                    row["role"] = role
                    row["display_name"] = username
                    row["updated_at"] = utcnow()
                    row["plan"] = self._normalize_membership_tier(row.get("plan"))
                    row["membership_tier"] = self._normalize_membership_tier(row.get("membership_tier") or row["plan"])
                    if email and not row.get("email"):
                        row["email"] = email
                        row["email_lower"] = email_lower
                    return self._public(row)
        document = {
            "display_name": username,
            "username": username,
            "email": email,
            "email_lower": email_lower or None,
            "password_hash": None,
            "role": role,
            "primary_role": role,
            "roles": [role],
            "active_role": role,
            "plan": "basic",
            "membership_tier": "basic",
            "account_status": "active",
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "last_login_at": None,
            "failed_login_count": 0,
        }
        user_id = self._insert("users", document)
        return {**document, "_id": user_id}

    @staticmethod
    def _normalize_email(email):
        return (email or "").strip().lower()

    def _user_collection(self):
        return self.db.users if self.db is not None else self._memory["users"]

    def _public_user(self, document):
        public = self._public(document)
        if public and public.get("email_lower") == "":
            public["email_lower"] = None
        return self._normalize_roles_document(public)

    def get_user_by_id(self, user_id):
        if not user_id:
            return None
        if self.db is not None:
            return self._public_user(self.db.users.find_one({"_id": self._id(user_id)}))
        for row in self._memory["users"]:
            if str(row.get("_id")) == str(user_id):
                return self._public_user(row)
        return None

    def get_user_by_email(self, email):
        email_lower = self._normalize_email(email)
        if not email_lower:
            return None
        if self.db is not None:
            return self._public_user(self.db.users.find_one({"email_lower": email_lower}))
        for row in self._memory["users"]:
            row_email = str(row.get("email_lower") or row.get("email") or "").lower()
            if row_email == email_lower:
                return self._public_user(row)
        return None

    def get_user_by_display_name(self, display_name):
        display_name = (display_name or "").strip()
        if not display_name:
            return None
        if self.db is not None:
            return self._public_user(self.db.users.find_one({"username": display_name}))
        for row in self._memory["users"]:
            if str(row.get("username", "")) == display_name:
                return self._public_user(row)
        return None

    def create_user(self, display_name, email, password_hash, role, account_status="active", roles=None, active_role=None, plan="basic", membership_tier=None):
        now = utcnow()
        assigned_roles = [str(value) for value in (roles or [role]) if value]
        if role not in assigned_roles:
            assigned_roles.insert(0, role)
        current_role = active_role if active_role in assigned_roles else role
        document = {
            "display_name": display_name.strip()[:80],
            "username": display_name.strip()[:80],
            "email": email.strip()[:120],
            "email_lower": self._normalize_email(email),
            "password_hash": password_hash,
            "role": current_role,
            "primary_role": role,
            "roles": assigned_roles,
            "active_role": current_role,
            "plan": self._normalize_membership_tier(plan),
            "membership_tier": self._normalize_membership_tier(membership_tier or plan),
            "account_status": account_status,
            "created_at": now,
            "updated_at": now,
            "last_login_at": None,
            "failed_login_count": 0,
        }
        user_id = self._insert("users", document)
        document["_id"] = user_id
        return document

    @staticmethod
    def _looks_like_password_hash(value):
        if not value or not isinstance(value, str):
            return False
        return value.startswith(("pbkdf2:", "scrypt:", "argon2:", "bcrypt:"))

    def update_user(self, user_id, updates):
        updates = {key: value for key, value in deepcopy(updates).items() if value is not None}
        if not updates:
            return self.get_user_by_id(user_id)
        if "roles" in updates and isinstance(updates["roles"], (list, tuple)):
            updates["roles"] = [str(value) for value in updates["roles"] if value]
        if "active_role" in updates and "roles" in updates and updates["active_role"] not in updates["roles"]:
            updates["active_role"] = updates["roles"][0] if updates["roles"] else updates.get("role")
        if "role" in updates and "active_role" not in updates:
            updates["active_role"] = updates["role"]
        if "active_role" in updates and "role" not in updates:
            updates["role"] = updates["active_role"]
        if "membership_tier" in updates:
            updates["membership_tier"] = self._normalize_membership_tier(updates["membership_tier"])
        if "plan" in updates:
            updates["plan"] = self._normalize_membership_tier(updates["plan"])
            updates["membership_tier"] = updates.get("membership_tier") or updates["plan"]
        updates["updated_at"] = utcnow()
        if self.db is not None:
            self.db.users.update_one({"_id": self._id(user_id)}, {"$set": updates})
        else:
            for row in self._memory["users"]:
                if str(row.get("_id")) == str(user_id):
                    row.update(updates)
                    break
        return self.get_user_by_id(user_id)

    def delete_user(self, user_id):
        if self.db is not None:
            result = self.db.users.delete_one({"_id": self._id(user_id)})
            return bool(result.deleted_count)
        before = len(self._memory["users"])
        self._memory["users"] = [row for row in self._memory["users"] if str(row.get("_id")) != str(user_id)]
        return len(self._memory["users"]) != before

    def record_login_success(self, user_id):
        return self.update_user(user_id, {"last_login_at": utcnow(), "failed_login_count": 0})

    def record_login_failure(self, user_id):
        user = self.get_user_by_id(user_id)
        count = int(user.get("failed_login_count") or 0) + 1 if user else 1
        return self.update_user(user_id, {"failed_login_count": count})

    def create_password_reset_token(self, user_id, token_hash, expires_at, requested_ip=None, user_agent_summary=None):
        now = utcnow()
        self.revoke_unused_password_reset_tokens(user_id, revoked_at=now)
        return self._insert("password_reset_tokens", {
            "user_id": self._id(user_id),
            "token_hash": token_hash,
            "created_at": now,
            "expires_at": expires_at,
            "used_at": None,
            "revoked_at": None,
            "requested_ip": requested_ip,
            "user_agent_summary": user_agent_summary,
            "status": "active",
        })

    def get_password_reset_token(self, token_hash):
        if not token_hash:
            return None
        if self.db is not None:
            return self._public(self.db.password_reset_tokens.find_one({"token_hash": token_hash}))
        for row in self._memory["password_reset_tokens"]:
            if row.get("token_hash") == token_hash:
                return self._public(row)
        return None

    def list_password_reset_tokens(self, user_id=None, limit=50):
        query = {"user_id": self._id(user_id)} if user_id is not None else {}
        return self._find("password_reset_tokens", query, limit=limit)

    def latest_password_reset_token(self, user_id):
        rows = self.list_password_reset_tokens(user_id, limit=1)
        return rows[0] if rows else None

    def revoke_unused_password_reset_tokens(self, user_id, exclude_token_id=None, revoked_at=None):
        now = revoked_at or utcnow()
        if self.db is not None:
            query = {"user_id": self._id(user_id), "used_at": None, "revoked_at": None}
            if exclude_token_id is not None:
                query["_id"] = {"$ne": self._id(exclude_token_id)}
            return self.db.password_reset_tokens.update_many(query, {"$set": {"revoked_at": now, "status": "revoked"}}).modified_count
        count = 0
        for row in self._memory["password_reset_tokens"]:
            if str(row.get("user_id")) != str(user_id) or row.get("used_at") is not None or row.get("revoked_at") is not None:
                continue
            if exclude_token_id is not None and str(row.get("_id")) == str(exclude_token_id):
                continue
            row.update(revoked_at=now, status="revoked", updated_at=now)
            count += 1
        return count

    def mark_password_reset_token_used(self, token_id, used_at=None):
        now = used_at or utcnow()
        if self.db is not None:
            result = self.db.password_reset_tokens.update_one(
                {"_id": self._id(token_id), "used_at": None, "revoked_at": None},
                {"$set": {"used_at": now, "status": "used"}},
            )
            return bool(result.modified_count)
        for row in self._memory["password_reset_tokens"]:
            if str(row.get("_id")) == str(token_id) and row.get("used_at") is None and row.get("revoked_at") is None:
                row.update(used_at=now, status="used", updated_at=now)
                return True
        return False

    def seed_admin_user(self, display_name, email, password, reset_password=False):
        display_name = (display_name or "Administrator").strip() or "Administrator"
        email_lower = self._normalize_email(email)
        if not email_lower or not password:
            raise ValueError("Administrator email and password are required.")
        existing = self.get_user_by_email(email_lower)
        if existing:
            updates = {
                "role": "administrator",
                "roles": sorted(set((existing.get("roles") or [existing.get("role") or "administrator"]) + ["administrator"])),
                "active_role": "administrator",
                "plan": self._normalize_membership_tier(existing.get("plan") or "professional"),
                "membership_tier": self._normalize_membership_tier(existing.get("membership_tier") or existing.get("plan") or "professional"),
                "account_status": "active",
                "email_lower": email_lower,
                "email": existing.get("email") or email_lower,
                "updated_at": utcnow(),
            }
            if not existing.get("display_name"):
                updates["display_name"] = display_name
            if reset_password:
                password_hash = password if self._looks_like_password_hash(password) else generate_password_hash(password)
                updates["password_hash"] = password_hash
            self.update_user(existing["_id"], updates)
            return self.get_user_by_email(email_lower)
        password_hash = password if self._looks_like_password_hash(password) else generate_password_hash(password)
        return self.create_user(display_name, email_lower, password_hash, "administrator", account_status="active", roles=["administrator"], active_role="administrator", plan="professional", membership_tier="professional")

    @staticmethod
    def _normalize_membership_tier(value):
        tier = (value or "basic").strip().lower() if isinstance(value, str) else "basic"
        if tier not in {"basic", "premium", "professional"}:
            return "basic"
        return tier

    def create_search(self, user_id, keyword, source, status="running", role=None, data_mode=None, **run_fields):
        search_run_id = str(run_fields.pop("search_run_id", "") or uuid.uuid4().hex)
        document = {
            "user_id": self._id(user_id),
            "search_run_id": search_run_id,
            "keyword": keyword,
            "raw_query": run_fields.pop("raw_query", keyword),
            "canonical_query": run_fields.pop("canonical_query", keyword),
            "workspace": run_fields.pop("workspace", "search"),
            "selected_source": source,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "result_count": 0,
            "status": status,
            "role": role,
            "data_mode": data_mode or ("synthetic" if source == "demo" else "api"),
        }
        document.update(deepcopy(run_fields))
        return self._insert("search_records", document)

    def complete_search(self, search_id, result_count, status="completed"):
        updates = {"result_count": result_count, "status": status, "updated_at": utcnow()}
        if self.db is not None:
            self.db.search_records.update_one(
                {"_id": self._id(search_id)},
                {"$set": updates},
            )
        else:
            for row in self._memory["search_records"]:
                if str(row["_id"]) == str(search_id):
                    row.update(updates)

    def update_search_run(self, search_id, updates):
        values = deepcopy(updates)
        values["updated_at"] = utcnow()
        if self.db is not None:
            self.db.search_records.update_one({"_id": self._id(search_id)}, {"$set": values})
        else:
            for row in self._memory["search_records"]:
                if str(row.get("_id")) == str(search_id):
                    row.update(values)
                    break

    def attach_market_results(self, search_id, record_ids, filters=None):
        values = {"market_record_ids": [self._id(value) for value in record_ids], "filters": deepcopy(filters or {})}
        if self.db is not None:
            self.db.search_records.update_one({"_id": self._id(search_id)}, {"$set": values})
        else:
            for row in self._memory["search_records"]:
                if str(row["_id"]) == str(search_id):
                    row.update(values)

    def attach_result_tokens(self, search_id, result_tokens, filters=None):
        values = {"result_tokens": list(result_tokens), "filters": deepcopy(filters or {})}
        if self.db is not None:
            self.db.search_records.update_one({"_id": self._id(search_id)}, {"$set": values})
        else:
            for row in self._memory["search_records"]:
                if str(row["_id"]) == str(search_id):
                    row.update(values)

    def update_search_source(self, search_id, source, data_mode):
        values = {"selected_source": source, "data_mode": data_mode}
        if self.db is not None:
            self.db.search_records.update_one({"_id": self._id(search_id)}, {"$set": values})
        else:
            for row in self._memory["search_records"]:
                if str(row["_id"]) == str(search_id):
                    row.update(values)

    def save_products(self, search_id, user_id, products):
        documents = []
        for product in products:
            row = deepcopy(product)
            row.pop("price_numeric", None)
            row.update(search_record_id=self._id(search_id), user_id=self._id(user_id))
            documents.append(row)
        if not documents:
            return 0
        if self.db is not None:
            self.db.product_results.insert_many(documents)
        else:
            for row in documents:
                row["_id"] = uuid.uuid4().hex
                self._memory["product_results"].append(row)
        return len(documents)

    def save_external_results(self, search_id, user_id, products):
        documents = []
        for product in products:
            row = deepcopy(product)
            row.pop("_id", None)
            row.pop("_selection_token", None)
            row.update(search_record_id=self._id(search_id), user_id=self._id(user_id), created_at=utcnow())
            documents.append(row)
        if not documents:
            return []
        if self.db is not None:
            result = self.db.search_results.insert_many(documents)
            for row, inserted_id in zip(documents, result.inserted_ids):
                row["_id"] = inserted_id
                row["result_id"] = str(inserted_id)
                self.db.search_results.update_one({"_id": inserted_id}, {"$set": {"result_id": str(inserted_id)}})
        else:
            for row in documents:
                row["_id"] = uuid.uuid4().hex
                row["result_id"] = str(row["_id"])
                self._memory["search_results"].append(row)
        return [self._public(row) for row in documents]

    def get_product_results_by_ids(self, record_ids):
        ids = [self._id(value) for value in record_ids]
        if not ids:
            return []
        if self.db is not None:
            rows = {str(row["_id"]): self._public(row) for row in self.db.search_results.find({"_id": {"$in": ids}})}
            return [rows[str(value)] for value in ids if str(value) in rows]
        rows = {str(row.get("_id")): self._public(row) for row in self._memory["search_results"]}
        return [rows[str(value)] for value in ids if str(value) in rows]

    def get_search_cache(self, cache_key):
        if not cache_key:
            return None
        if self.db is not None:
            return self._public(self.db.search_cache.find_one({"cache_key": cache_key}))
        for row in self._memory["search_cache"]:
            if row.get("cache_key") == cache_key:
                return self._public(row)
        return None

    def save_search_cache(self, cache_key, payload):
        document = deepcopy(payload)
        document["cache_key"] = cache_key
        document["updated_at"] = utcnow()
        document.setdefault("created_at", utcnow())
        if self.db is not None:
            updates = deepcopy(document)
            created_at = updates.pop("created_at", utcnow())
            self.db.search_cache.update_one({"cache_key": cache_key}, {"$set": updates, "$setOnInsert": {"created_at": created_at}}, upsert=True)
            row = self.db.search_cache.find_one({"cache_key": cache_key})
            return self._public(row)
        for row in self._memory["search_cache"]:
            if row.get("cache_key") == cache_key:
                row.update(document)
                return self._public(row)
        document["_id"] = uuid.uuid4().hex
        self._memory["search_cache"].append(document)
        return self._public(document)

    def log_ai_search(self, user_id, keyword, model, prompt_summary, raw_response_summary):
        return self._insert("ai_search_logs", {
            "user_id": self._id(user_id),
            "keyword": keyword,
            "model": model,
            "prompt_summary": prompt_summary[:500],
            "raw_response_summary": raw_response_summary[:1000],
            "created_at": utcnow(),
        })

    def save_ai_insight(self, user_id, payload):
        document = deepcopy(payload)
        document.update(
            ai_insight_id=document.get("ai_insight_id") or uuid.uuid4().hex,
            user_id=self._id(user_id),
            search_run_id=str(document.get("search_run_id") or ""),
            generated_at=document.get("generated_at") or utcnow(),
        )
        return self._insert("ai_insights", document)

    def get_ai_insight_for_search(self, search_run_id, user_id):
        query = {"search_run_id": str(search_run_id), "user_id": self._id(user_id)}
        rows = self._find("ai_insights", query, limit=1)
        return rows[0] if rows else None

    def list_searches(self, user_id=None, limit=20):
        query = {"user_id": self._id(user_id)} if user_id else {}
        return self._find("search_records", query, limit=limit)

    def get_search(self, search_id, user_id=None):
        scope = {"user_id": self._id(user_id)} if user_id is not None else {}
        public_query = {**scope, "search_run_id": str(search_id)}
        if self.db is not None:
            row = self._public(self.db.search_records.find_one(public_query))
            if not row:
                row = self._public(self.db.search_records.find_one({**scope, "_id": self._id(search_id)}))
            return row if row and not row.get("is_deleted") else None
        rows = self._find("search_records", public_query)
        if not rows:
            rows = self._find("search_records", {**scope, "_id": self._id(search_id)})
        return rows[0] if rows else None

    def find_latest_search(self, user_id, keyword):
        query = {"user_id": self._id(user_id), "keyword": keyword}
        rows = self._find("search_records", query, limit=1)
        return rows[0] if rows else None

    def get_products(self, search_id):
        return self._find("product_results", {"search_record_id": self._id(search_id)}, descending=False)

    def list_products(self, user_id=None, limit=20):
        query = {"user_id": self._id(user_id)} if user_id else {}
        return self._find("product_results", query, limit=limit)

    def search_market_records(self, keyword, platform=None, category=None, min_price=None, max_price=None, sort="normalized_price_asc", limit=100):
        import re
        query = {}
        if keyword:
            pattern = re.escape(keyword.strip())
            query["$or"] = [{"query": {"$regex": pattern, "$options": "i"}}, {"product_name": {"$regex": pattern, "$options": "i"}}]
        if platform:
            query["platform"] = platform
        if category:
            query["category"] = category
        price_filter = {}
        if min_price is not None:
            price_filter["$gte"] = float(min_price)
        if max_price is not None:
            price_filter["$lte"] = float(max_price)
        if price_filter:
            query["normalized_price"] = price_filter
        sort_map = {"normalized_price_asc": ("normalized_price", ASCENDING), "normalized_price_desc": ("normalized_price", DESCENDING), "platform": ("platform", ASCENDING), "confidence": ("confidence", DESCENDING)}
        sort_field, direction = sort_map.get(sort, sort_map["normalized_price_asc"])
        if self.db is not None:
            cursor = self.db.market_records.find(query).sort(sort_field, direction).limit(limit)
            return [self._public(row) for row in cursor]
        rows = self._memory["market_records"]
        words = keyword.lower().split() if keyword else []
        def accepted(row):
            text = f"{row.get('query','')} {row.get('product_name','')}".lower()
            price = row.get("normalized_price")
            return (not words or all(word in text for word in words)) and (not platform or row.get("platform") == platform) and (not category or row.get("category") == category) and (min_price is None or price is not None and price >= min_price) and (max_price is None or price is not None and price <= max_price)
        result = [deepcopy(row) for row in rows if accepted(row)]
        result.sort(key=lambda row: row.get(sort_field, "") if row.get(sort_field) is not None else float("inf"), reverse=direction == DESCENDING)
        return [self._public(row) for row in result[:limit]]

    def get_market_records(self, record_ids):
        ids = [self._id(value) for value in record_ids]
        if not ids:
            return []
        if self.db is not None:
            rows = {str(row["_id"]): self._public(row) for row in self.db.market_records.find({"_id": {"$in": ids}})}
            return [rows[str(value)] for value in ids if str(value) in rows]
        rows = {str(row.get("_id")): self._public(row) for row in self._memory["market_records"]}
        return [rows[str(value)] for value in ids if str(value) in rows]

    def store_fallback_market_records(self, records):
        if self.db is not None:
            return records
        stored = []
        for record in records:
            row = deepcopy(record)
            row["_id"] = row.get("_id") or uuid.uuid4().hex
            self._memory["market_records"].append(row)
            stored.append(self._public(row))
        return stored

    def market_facets(self):
        if self.db is not None:
            return {"platforms": sorted(self.db.market_records.distinct("platform")), "categories": sorted(self.db.market_records.distinct("category"))}
        return {"platforms": sorted({row.get("platform") for row in self._memory["market_records"] if row.get("platform")}), "categories": sorted({row.get("category") for row in self._memory["market_records"] if row.get("category")})}

    def save_evidence(self, user_id, search_id, product):
        """Save a deliberate evidence bookmark, avoiding duplicate products per user."""
        product = deepcopy(product)
        identity = {
            "user_id": self._id(user_id),
            "search_record_id": self._id(search_id),
            "title": product.get("title"),
            "platform": product.get("platform"),
        }
        if self.db is not None:
            existing = self.db.evidence_records.find_one({**identity, "is_deleted": {"$ne": True}})
            if existing:
                return str(existing["_id"]), False
        else:
            if any(all(str(row.get(k)) == str(v) for k, v in identity.items()) and not row.get("is_deleted") for row in self._memory["evidence_records"]):
                return "", False
        product.pop("_id", None)
        product.update(identity)
        product.update(saved_at=utcnow(), created_at=utcnow(), evidence_status="saved", mongodb_status="persisted" if self.db is not None else "memory fallback")
        return self._insert("evidence_records", product), True

    def list_evidence(self, user_id=None, limit=50):
        query = {"user_id": self._id(user_id)} if user_id else {}
        return self._find("evidence_records", query, limit=limit)

    def get_evidence(self, evidence_id, user_id=None):
        query = {"_id": self._id(evidence_id)}
        if user_id is not None:
            query["user_id"] = self._id(user_id)
        if self.db is not None:
            row = self._public(self.db.evidence_records.find_one(query))
            return row if row and not row.get("is_deleted") else None
        rows = self._find("evidence_records", query, limit=1)
        return rows[0] if rows else None

    def save_research(self, user_id, research):
        document = deepcopy(research)
        document.update(user_id=self._id(user_id), created_at=utcnow(), storage_mode=self.mode)
        return self._insert("research_records", document)

    def list_research(self, user_id=None, limit=50):
        query = {"user_id": self._id(user_id)} if user_id else {}
        return self._find("research_records", query, limit=limit)

    def get_research(self, research_id, user_id=None):
        query = {"_id": self._id(research_id)}
        if user_id is not None:
            query["user_id"] = self._id(user_id)
        if self.db is not None:
            row = self._public(self.db.research_records.find_one(query))
            return row if row and not row.get("is_deleted") else None
        rows = self._find("research_records", query, limit=1)
        return rows[0] if rows else None

    def create_comparison_group(self, user_id, payload):
        document = deepcopy(payload)
        document.update(
            user_id=self._id(user_id),
            search_record_id=self._id(payload.get("search_record_id")) if payload.get("search_record_id") else None,
            selected_record_ids=[str(value) for value in payload.get("selected_record_ids", [])],
            selected_records=deepcopy(payload.get("selected_records") or []),
            source_types=sorted({str(value) for value in payload.get("source_types", []) if value}),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        return self._insert("comparison_groups", document)

    def get_comparison_group(self, comparison_id, user_id=None):
        query = {"_id": self._id(comparison_id)}
        if user_id is not None:
            query["user_id"] = self._id(user_id)
        if self.db is not None:
            row = self._public(self.db.comparison_groups.find_one(query))
            return row if row and not row.get("is_deleted") else None
        rows = self._find("comparison_groups", query, limit=1)
        return rows[0] if rows else None

    def update_comparison_group(self, comparison_id, updates):
        return self._soft_update("comparison_groups", comparison_id, deepcopy(updates))

    def list_comparison_groups(self, user_id=None, saved_only=False, limit=50):
        query = {"user_id": self._id(user_id)} if user_id else {}
        if saved_only:
            query["saved_status"] = "saved"
        return self._find("comparison_groups", query, limit=limit)

    def create_analysis_record(self, user_id, payload):
        document = deepcopy(payload)
        document.update(
            document_type="analysis_record",
            user_id=self._id(user_id),
            search_record_id=self._id(payload.get("search_record_id")) if payload.get("search_record_id") else None,
            comparison_set_id=self._id(payload.get("comparison_set_id")) if payload.get("comparison_set_id") else None,
            included_result_ids=[str(value) for value in payload.get("included_result_ids", [])],
            excluded_result_ids=[str(value) for value in payload.get("excluded_result_ids", [])],
            created_at=utcnow(),
            status=payload.get("status") or "active",
            saved_status=payload.get("saved_status") or "saved",
        )
        # Older soft-deleted records retained their active unique signature.
        # Retire those tombstones lazily before inserting a replacement while
        # preserving the original signature for audit/history purposes.
        signature = document.get("analysis_signature")
        if signature:
            legacy_query = {
                "document_type": "analysis_record",
                "user_id": self._id(user_id),
                "analysis_signature": signature,
                "status": "deleted",
            }
            if self.db is not None:
                self.db.analytics_reports.update_many(
                    legacy_query,
                    {"$set": {"deleted_analysis_signature": signature, "updated_at": utcnow()}, "$unset": {"analysis_signature": ""}},
                )
            else:
                for row in self._memory["analytics_reports"]:
                    if all(str(row.get(key)) == str(value) for key, value in legacy_query.items()):
                        row["deleted_analysis_signature"] = row.pop("analysis_signature")
                        row["updated_at"] = utcnow()
        return self._insert("analytics_reports", document)

    def find_analysis_by_signature(self, user_id, signature):
        if not signature:
            return None
        query = {"document_type": "analysis_record", "user_id": self._id(user_id), "analysis_signature": signature, "status": {"$ne": "deleted"}}
        if self.db is not None:
            return self._public(self.db.analytics_reports.find_one(query))
        rows = self._find("analytics_reports", query, limit=1)
        return next((row for row in rows if row.get("status") != "deleted"), None)

    def touch_analysis_record(self, analysis_id):
        record = self.get_analysis_record(analysis_id)
        if not record:
            return None
        updates = {"last_viewed_at": utcnow(), "view_count": int(record.get("view_count") or 0) + 1}
        self._soft_update("analytics_reports", analysis_id, updates)
        return self.get_analysis_record(analysis_id)

    def get_analysis_record(self, analysis_id, user_id=None):
        query = {"_id": self._id(analysis_id), "document_type": "analysis_record", "status": {"$ne": "deleted"}}
        if user_id is not None:
            query["user_id"] = self._id(user_id)
        if self.db is not None:
            return self._public(self.db.analytics_reports.find_one(query))
        rows = self._find("analytics_reports", query, limit=1)
        return next((row for row in rows if row.get("status") != "deleted"), None)

    def update_analysis_record(self, analysis_id, updates):
        return self._soft_update("analytics_reports", analysis_id, deepcopy(updates))

    def delete_analysis_record(self, analysis_id, deleted_by):
        deleted_at = utcnow()
        query = {"_id": self._id(analysis_id), "user_id": self._id(deleted_by), "document_type": "analysis_record"}
        if self.db is not None:
            record = self.db.analytics_reports.find_one(query)
            if not record:
                return False
            updates = {"status": "deleted", "deleted_at": deleted_at, "deleted_by": self._id(deleted_by), "updated_at": deleted_at}
            update_document = {"$set": updates}
            if record.get("analysis_signature"):
                updates["deleted_analysis_signature"] = record["analysis_signature"]
                update_document["$unset"] = {"analysis_signature": ""}
            self.db.analytics_reports.update_one(query, update_document)
            return updates
        for row in self._memory["analytics_reports"]:
            if str(row.get("_id")) != str(analysis_id) or str(row.get("user_id")) != str(deleted_by):
                continue
            if row.get("analysis_signature"):
                row["deleted_analysis_signature"] = row.pop("analysis_signature")
            row.update({"status": "deleted", "deleted_at": deleted_at, "deleted_by": self._id(deleted_by), "updated_at": deleted_at})
            return True
        return False

    def list_analysis_records(self, user_id=None, limit=50):
        query = {"document_type": "analysis_record", "status": {"$ne": "deleted"}}
        if user_id is not None:
            query["user_id"] = self._id(user_id)
        return [row for row in self._find("analytics_reports", query, limit=limit) if row.get("status") != "deleted"]

    def delete_evidence(self, evidence_id, deleted_by=None, reason=None):
        now = utcnow()
        return self._soft_update("evidence_records", evidence_id, {
            "is_deleted": True,
            "deleted_at": now,
            "deleted_by": self._id(deleted_by) if deleted_by else None,
            "delete_reason": reason or "user_requested",
        })

    def delete_research(self, research_id, deleted_by=None, reason=None):
        now = utcnow()
        return self._soft_update("research_records", research_id, {
            "is_deleted": True,
            "deleted_at": now,
            "deleted_by": self._id(deleted_by) if deleted_by else None,
            "delete_reason": reason or "user_requested",
        })

    def create_watchlist_item(self, user_id, payload):
        now = utcnow()
        document = {
            "user_id": self._id(user_id),
            "tracking_mode": payload.get("tracking_mode") or "search_scope",
            "keyword": (payload.get("keyword") or "").strip(),
            "product_label": (payload.get("product_label") or payload.get("keyword") or "").strip(),
            "platform_scope": payload.get("platform_scope") or "all",
            "category": payload.get("category") or "All",
            "category_key": payload.get("category_key"),
            "category_display": payload.get("category_display"),
            "condition_scope": payload.get("condition_scope"),
            "source_scope": payload.get("source_scope") or "search",
            "record_scope": payload.get("record_scope") or ("selected_comparison_records" if payload.get("tracking_mode") == "selected_records" else "all_current_search_results"),
            "data_source_label": payload.get("data_source_label") or "live",
            "status": payload.get("status") or "active",
            "created_at": now,
            "updated_at": now,
            "search_record_id": self._id(payload.get("search_record_id")) if payload.get("search_record_id") else None,
            "comparison_group_id": self._id(payload.get("comparison_group_id")) if payload.get("comparison_group_id") else None,
            "selected_record_ids": [self._id(value) for value in payload.get("selected_record_ids", [])],
            "selected_records_snapshot": deepcopy(payload.get("selected_records_snapshot") or []),
            "initial_records_snapshot": deepcopy(payload.get("initial_records_snapshot") or payload.get("selected_records_snapshot") or []),
            "frozen_scope": deepcopy(payload.get("frozen_scope") or {}),
            "comparison_record_ids": [self._id(value) for value in payload.get("comparison_record_ids", [])],
            "source_label": payload.get("source_label") or payload.get("data_source_label") or "live",
            "monitor_id": payload.get("monitor_id") or uuid.uuid4().hex,
            "comparison_set_id": self._id(payload.get("comparison_set_id") or payload.get("comparison_group_id")) if (payload.get("comparison_set_id") or payload.get("comparison_group_id")) else None,
            "auto_refresh_enabled": False,
            "auto_refresh_ever_enabled": False,
            "refresh_frequency": "daily",
            "refresh_timezone": payload.get("refresh_timezone") or "Asia/Singapore",
            "last_refreshed_at": None,
            "next_refresh_at": None,
            "last_refresh_status": "never",
            "last_refresh_error_code": None,
            "last_refresh_trigger": None,
            "last_scheduled_refresh_date": None,
            "alert_enabled": False,
            "alert_direction": "drop",
            "alert_threshold_percent": None,
            "alert_email_enabled": True,
            "alert_cooldown_hours": 24,
            "alert_last_evaluated_at": None,
            "alert_last_change_percent": None,
            "alert_last_triggered_at": None,
            "alert_last_email_status": None,
            "alert_rule_updated_at": None,
            "alert_rule_version": 0,
        }
        # Each tracked comparison is a distinct monitor. Query text is display
        # metadata, never an identity/upsert key.
        return self._insert("watchlist_items", document), True

    @staticmethod
    def _watchlist_refresh_defaults(row):
        if not row:
            return row
        row = deepcopy(row)
        row.setdefault("auto_refresh_enabled", False)
        row.setdefault("auto_refresh_ever_enabled", False)
        row.setdefault("refresh_frequency", "daily")
        row.setdefault("refresh_timezone", "Asia/Singapore")
        row.setdefault("last_refreshed_at", None)
        row.setdefault("next_refresh_at", None)
        row.setdefault("last_refresh_status", "never")
        row.setdefault("last_refresh_error_code", None)
        row.setdefault("last_refresh_trigger", None)
        row.setdefault("last_scheduled_refresh_date", None)
        row.setdefault("alert_enabled", False)
        row.setdefault("alert_direction", "drop")
        row.setdefault("alert_threshold_percent", None)
        row.setdefault("alert_email_enabled", True)
        row.setdefault("alert_cooldown_hours", 24)
        row.setdefault("alert_last_evaluated_at", None)
        row.setdefault("alert_last_change_percent", None)
        row.setdefault("alert_last_triggered_at", None)
        row.setdefault("alert_last_email_status", None)
        row.setdefault("alert_rule_updated_at", None)
        row.setdefault("alert_rule_version", 0)
        return row

    def list_watchlist_items(self, user_id=None, include_archived=False, limit=50):
        query = {"user_id": self._id(user_id)} if user_id else {}
        if not include_archived:
            query["status"] = {"$nin": ["deleted", "archived"]}
        return [self._watchlist_refresh_defaults(row) for row in self._find("watchlist_items", query, limit=limit)]

    def get_watchlist_item(self, watchlist_id, user_id=None):
        query = {"_id": self._id(watchlist_id)}
        if user_id is not None:
            query["user_id"] = self._id(user_id)
        if self.db is not None:
            row = self._public(self.db.watchlist_items.find_one(query))
            return self._watchlist_refresh_defaults(row) if row else None
        rows = self._find("watchlist_items", query, limit=1)
        return self._watchlist_refresh_defaults(rows[0]) if rows else None

    def update_watchlist_item(self, watchlist_id, updates, user_id=None):
        updates = deepcopy(updates)
        updates["updated_at"] = utcnow()
        query = {"_id": self._id(watchlist_id)}
        if user_id is not None:
            query["user_id"] = self._id(user_id)
        if self.db is not None:
            result = self.db.watchlist_items.update_one(query, {"$set": updates})
            return bool(result.matched_count)
        for row in self._memory["watchlist_items"]:
            if str(row.get("_id")) == str(watchlist_id) and (user_id is None or str(row.get("user_id")) == str(user_id)):
                row.update(updates)
                return True
        return False

    def enabled_auto_refresh_monitor(self, user_id, exclude_id=None):
        monitors = self.list_watchlist_items(user_id, include_archived=True, limit=0)
        return next((
            row for row in monitors
            if row.get("status") == "active"
            and row.get("auto_refresh_enabled") is True
            and (exclude_id is None or str(row.get("_id")) != str(exclude_id))
        ), None)

    def enable_watchlist_auto_refresh(self, watchlist_id, user_id, next_refresh_at, timezone_name="Asia/Singapore"):
        with self._monitor_refresh_lock:
            monitor = self.get_watchlist_item(watchlist_id, user_id)
            if not monitor or monitor.get("status") != "active":
                return False, None, "ineligible"
            current = self.enabled_auto_refresh_monitor(user_id, exclude_id=watchlist_id)
            if current:
                return False, current, "limit"
            try:
                updated = self.update_watchlist_item(watchlist_id, {
                    "auto_refresh_enabled": True,
                    "auto_refresh_ever_enabled": True,
                    "refresh_frequency": "daily",
                    "refresh_timezone": timezone_name,
                    "next_refresh_at": next_refresh_at,
                    "last_refresh_error_code": None,
                }, user_id=user_id)
            except DuplicateKeyError:
                return False, self.enabled_auto_refresh_monitor(user_id, exclude_id=watchlist_id), "limit"
            return updated, None, None if updated else "ineligible"

    def pause_watchlist_auto_refresh(self, watchlist_id, user_id=None):
        return self.update_watchlist_item(watchlist_id, {
            "auto_refresh_enabled": False,
            "next_refresh_at": None,
        }, user_id=user_id)

    def archive_watchlist_item(self, watchlist_id, user_id=None):
        return self.update_watchlist_item(watchlist_id, {"status": "archived", "auto_refresh_enabled": False, "next_refresh_at": None}, user_id=user_id)

    def restore_watchlist_item(self, watchlist_id, user_id=None):
        return self.update_watchlist_item(watchlist_id, {"status": "active", "auto_refresh_enabled": False, "next_refresh_at": None}, user_id=user_id)

    def delete_watchlist_item(self, watchlist_id, user_id=None):
        return self.update_watchlist_item(watchlist_id, {"status": "deleted", "auto_refresh_enabled": False, "next_refresh_at": None}, user_id=user_id)

    def claim_scheduled_monitor_refresh(self, monitor_id, owner_id, scheduled_date):
        document = {
            "monitor_id": self._id(monitor_id),
            "owner_id": self._id(owner_id),
            "scheduled_date": str(scheduled_date),
            "trigger": "scheduled",
            "status": "started",
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        with self._monitor_refresh_lock:
            if self.db is not None:
                try:
                    self.db.monitor_refresh_claims.insert_one(document)
                    return True
                except DuplicateKeyError:
                    return False
            duplicate = next((row for row in self._memory["monitor_refresh_claims"] if
                              str(row.get("monitor_id")) == str(monitor_id)
                              and row.get("scheduled_date") == str(scheduled_date)
                              and row.get("trigger") == "scheduled"), None)
            if duplicate:
                return False
            document["_id"] = uuid.uuid4().hex
            self._memory["monitor_refresh_claims"].append(document)
            return True

    def complete_scheduled_monitor_refresh(self, monitor_id, scheduled_date, status):
        query = {"monitor_id": self._id(monitor_id), "scheduled_date": str(scheduled_date), "trigger": "scheduled"}
        updates = {"status": status, "updated_at": utcnow()}
        if self.db is not None:
            self.db.monitor_refresh_claims.update_one(query, {"$set": updates})
            return
        with self._monitor_refresh_lock:
            for row in self._memory["monitor_refresh_claims"]:
                if str(row.get("monitor_id")) == str(monitor_id) and row.get("scheduled_date") == str(scheduled_date) and row.get("trigger") == "scheduled":
                    row.update(updates)
                    return

    def save_price_snapshot(self, watchlist_id, user_id, snapshot):
        document = deepcopy(snapshot)
        watchlist_oid = self._id(watchlist_id)
        user_oid = self._id(user_id)
        monitor = self.get_watchlist_item(watchlist_id, user_id)
        stable_monitor_id = (monitor or {}).get("monitor_id") or watchlist_id
        document.setdefault("snapshot_change_status", "changed")
        document.update({
            "watchlist_id": watchlist_oid,
            "monitor_id": self._id(snapshot.get("monitor_id") or stable_monitor_id),
            "user_id": user_oid,
            "created_at": utcnow(),
        })
        return self._insert("price_snapshots", document)

    def create_price_alert_event(self, event):
        """Atomically reserve one threshold event for a snapshot/rule version."""
        document = deepcopy(event)
        document["monitor_id"] = self._id(document.get("monitor_id"))
        document["owner_user_id"] = self._id(document.get("owner_user_id"))
        document["snapshot_id"] = self._id(document.get("snapshot_id"))
        if document.get("previous_snapshot_id"):
            document["previous_snapshot_id"] = self._id(document["previous_snapshot_id"])
        document.setdefault("created_at", utcnow())
        with self._price_alert_lock:
            if self.db is not None:
                try:
                    return str(self.db.price_alert_events.insert_one(document).inserted_id), True
                except DuplicateKeyError:
                    existing = self.db.price_alert_events.find_one({
                        "monitor_id": document["monitor_id"],
                        "snapshot_id": document["snapshot_id"],
                        "alert_rule_version": document.get("alert_rule_version", 0),
                    })
                    return str((existing or {}).get("_id", "")), False
            duplicate = next((row for row in self._memory["price_alert_events"] if
                              str(row.get("monitor_id")) == str(document.get("monitor_id"))
                              and str(row.get("snapshot_id")) == str(document.get("snapshot_id"))
                              and row.get("alert_rule_version", 0) == document.get("alert_rule_version", 0)), None)
            if duplicate:
                return str(duplicate.get("_id")), False
            document["_id"] = uuid.uuid4().hex
            self._memory["price_alert_events"].append(document)
            return document["_id"], True

    def update_price_alert_event(self, event_id, updates):
        self._soft_update("price_alert_events", event_id, updates)
        return True

    def list_price_alert_events(self, owner_user_id, monitor_id, limit=50):
        return self._find("price_alert_events", {
            "owner_user_id": self._id(owner_user_id),
            "monitor_id": self._id(monitor_id),
        }, limit=limit)

    def list_price_snapshots(self, watchlist_id, limit=50):
        return self._find("price_snapshots", {"watchlist_id": self._id(watchlist_id)}, limit=limit)

    def latest_price_snapshot(self, watchlist_id):
        rows = self._find("price_snapshots", {"watchlist_id": self._id(watchlist_id)}, limit=1)
        return rows[0] if rows else None

    def count_price_snapshots(self, watchlist_id):
        query = {"watchlist_id": self._id(watchlist_id)}
        if self.db is not None:
            return self.db.price_snapshots.count_documents(query)
        return sum(1 for row in self._memory["price_snapshots"] if str(row.get("watchlist_id")) == str(query["watchlist_id"]))

    def save_prediction(self, user_id, watchlist_id, prediction):
        document = strip_mongo_id(prediction)
        document.update({
            "user_id": self._id(user_id),
            "owner_user_id": self._id(user_id),
            "watchlist_id": self._id(watchlist_id),
            "monitor_id": self._id(prediction.get("monitor_id") or watchlist_id),
        })
        return self._insert("prediction_records", document)

    def list_predictions(self, user_id=None, watchlist_id=None, limit=50):
        query = {}
        if user_id is not None:
            query["user_id"] = self._id(user_id)
        if watchlist_id is not None:
            query["watchlist_id"] = self._id(watchlist_id)
        return self._find("prediction_records", query, limit=limit)

    def update_prediction(self, prediction_id, updates):
        return self._soft_update("prediction_records", prediction_id, strip_mongo_id(updates))

    def find_mixed_monitor_snapshots(self, user_id=None):
        """Return monitors whose snapshots contain more than one selected group.

        This is deliberately read-only so it is safe for development audits.
        """
        monitors = self.list_watchlist_items(user_id=user_id, include_archived=True, limit=0)
        affected = []
        for monitor in monitors:
            snapshots = self.list_price_snapshots(monitor["_id"], limit=0)
            groups = {tuple(sorted(map(str, row.get("selected_result_ids") or []))) for row in snapshots if row.get("selected_result_ids")}
            if len(groups) > 1:
                affected.append({"monitor_id": monitor.get("monitor_id") or monitor["_id"], "watchlist_id": monitor["_id"], "snapshot_count": len(snapshots), "selected_groups": len(groups)})
        return affected

    def delete_monitor_with_history(self, watchlist_id, user_id=None):
        """Development cleanup only: remove a monitor and its snapshots/predictions."""
        query = {"_id": self._id(watchlist_id)}
        if user_id is not None:
            query["user_id"] = self._id(user_id)
        if self.db is not None:
            monitor = self.db.watchlist_items.find_one(query)
            if not monitor:
                return False
            self.db.price_snapshots.delete_many({"watchlist_id": monitor["_id"]})
            self.db.prediction_records.delete_many({"watchlist_id": monitor["_id"]})
            self.db.watchlist_items.delete_one({"_id": monitor["_id"]})
            return True
        monitor = next((row for row in self._memory["watchlist_items"] if str(row.get("_id")) == str(watchlist_id) and (user_id is None or str(row.get("user_id")) == str(user_id))), None)
        if not monitor:
            return False
        monitor_id = str(monitor["_id"])
        self._memory["price_snapshots"] = [row for row in self._memory["price_snapshots"] if str(row.get("watchlist_id")) != monitor_id]
        self._memory["prediction_records"] = [row for row in self._memory["prediction_records"] if str(row.get("watchlist_id")) != monitor_id]
        self._memory["watchlist_items"].remove(monitor)
        return True

    def delete_search(self, search_id, deleted_by=None, reason=None):
        now = utcnow()
        return self._soft_update("search_records", search_id, {
            "is_deleted": True,
            "deleted_at": now,
            "deleted_by": self._id(deleted_by) if deleted_by else None,
            "delete_reason": reason or "user_requested",
        })

    def delete_many_searches(self, search_ids, deleted_by=None, reason=None):
        count = 0
        for search_id in search_ids:
            self.delete_search(search_id, deleted_by=deleted_by, reason=reason)
            count += 1
        return count

    def archive_audit_log(self, log_id, archived_by=None, reason=None):
        now = utcnow()
        return self._soft_update("audit_logs", log_id, {
            "is_archived": True,
            "archived_at": now,
            "archived_by": self._id(archived_by) if archived_by else None,
            "archive_reason": reason or "archive_selected",
        })

    def archive_activity_log(self, log_id, archived_by=None, reason=None):
        now = utcnow()
        return self._soft_update("activity_logs", log_id, {
            "is_archived": True,
            "archived_at": now,
            "archived_by": self._id(archived_by) if archived_by else None,
            "archive_reason": reason or "archive_selected",
        })

    def archive_many_activity_logs(self, log_ids, archived_by=None, reason=None):
        count = 0
        for log_id in log_ids:
            self.archive_activity_log(log_id, archived_by=archived_by, reason=reason)
            count += 1
        return count

    def archive_many_audit_logs(self, log_ids, archived_by=None, reason=None):
        count = 0
        for log_id in log_ids:
            self.archive_audit_log(log_id, archived_by=archived_by, reason=reason)
            count += 1
        return count

    def restore_demo_test_records(self):
        if self.db is not None:
            self.db.testing_records.update_many({}, {"$set": {"is_archived": False, "is_deleted": False, "updated_at": utcnow()}})
        else:
            for row in self._memory["testing_records"]:
                row["is_archived"] = False
                row["is_deleted"] = False
                row["updated_at"] = utcnow()

    def seed_testing_records(self, records):
        if self.db is not None and self.db.testing_records.count_documents({}) == 0:
            self.db.testing_records.insert_many(deepcopy(records))
            return
        if self.db is None and not self._memory["testing_records"]:
            for record in records:
                row = deepcopy(record)
                row["_id"] = row.get("_id") or uuid.uuid4().hex
                self._memory["testing_records"].append(row)

    def list_testing_records(self, limit=20):
        return self._find("testing_records", {"is_deleted": {"$ne": True}}, limit=limit)

    def delete_testing_records(self, record_ids, deleted_by=None, reason=None):
        count = 0
        for record_id in record_ids:
            self._soft_update("testing_records", record_id, {
                "is_deleted": True,
                "deleted_at": utcnow(),
                "deleted_by": self._id(deleted_by) if deleted_by else None,
                "delete_reason": reason or "test_data_deleted",
            })
            count += 1
        return count

    def log_event(self, user_id, event_type, role=None, display_name=None, details=None):
        now = utcnow()
        return self._insert("audit_logs", {
            "user_id": self._id(user_id) if user_id else None,
            "event_type": event_type,
            "action": event_type,
            "role": role,
            "display_name": display_name,
            "details": deepcopy(details or {}),
            "message": event_type.replace("_", " ").title(),
            "created_at": now,
            "timestamp": now,
            "demo_mode": False,
        })

    def list_audit_logs(self, user_id=None, limit=100):
        query = {"user_id": self._id(user_id)} if user_id else {}
        return self._find("audit_logs", query, limit=limit)

    def log_ai_activity(self, user_id, query, model, summary_source, record_count, fallback_reason=None):
        return self._insert("activity_logs", {
            "user_id": self._id(user_id), "action": "ai_discover", "query": query, "provider": "Gemini",
            "model": model, "summary_source": summary_source, "record_count": record_count,
            "fallback_reason": fallback_reason, "created_at": utcnow(),
        })

    def list_activity_logs(self, user_id=None, limit=100):
        query = {"user_id": self._id(user_id)} if user_id is not None else {}
        return self._find("activity_logs", query, limit=limit)

    def price_snapshot(self, user_id=None):
        products = self.list_products(user_id=user_id, limit=0)
        prices = []
        for product in products:
            try:
                prices.append(float(product.get("price")))
            except (TypeError, ValueError):
                continue
        return {
            "listings": len(prices),
            "lowest": round(min(prices), 2) if prices else None,
            "average": round(sum(prices) / len(prices), 2) if prices else None,
            "highest": round(max(prices), 2) if prices else None,
        }

    def save_report(self, report):
        existing = None
        if self.db is not None:
            existing = self.db.analytics_reports.find_one({"search_record_id": self._id(report["search_record_id"])})
            if existing:
                self.db.analytics_reports.update_one({"_id": existing["_id"]}, {"$set": report})
                return str(existing["_id"])
        else:
            for row in self._memory["analytics_reports"]:
                if str(row.get("search_record_id")) == str(report["search_record_id"]):
                    row.update(deepcopy(report))
                    return str(row["_id"])
        return self._insert("analytics_reports", report)

    def admin_snapshot(self):
        def count(name):
            return self.db[name].count_documents({}) if self.db is not None else len(self._memory[name])
        return {name: count(name) for name in self.collection_names}

    def list_ai_logs(self, user_id=None, limit=20):
        query = {"user_id": self._id(user_id)} if user_id is not None else {}
        return self._find("ai_search_logs", query, limit=limit)

    def list_users(self, limit=20):
        return [self._normalize_roles_document(row) for row in self._find("users", limit=limit)]

    def platform_distribution(self, user_id=None):
        query = {"user_id": self._id(user_id)} if user_id else {}
        products = self._find("product_results", query)
        return dict(Counter(item.get("platform") or "Unknown" for item in products))
