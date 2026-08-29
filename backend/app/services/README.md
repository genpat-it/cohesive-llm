# Application Services (`backend/app/services/`) - Auth & Rate Limiting

The `backend/app/services/` directory contains application-level services supporting authentication, security tokens, and API rate limiting.

---

## 1. Modules Specification

### 1.1 `auth.py` — Authentication & JWT Token Management
- **Password Hashing**: Provides `verify_password(plain, hashed)` and `get_password_hash(password)` using `passlib.context.CryptContext` with bcrypt.
- **JWT Token Generation**: Provides `create_access_token(data, expires_delta)` encoding HS256 JWT tokens.
- **FastAPI Dependency**: Provides `get_current_user` resolving Bearer tokens from incoming HTTP request headers.

### 1.2 `rate_limit.py` — Client Rate Limiting
- Implements in-memory token-bucket rate limiting per IP address/authenticated user.
- Prevents accidental denial-of-service or burst traffic from flooding the LLM inference backend.
