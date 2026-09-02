# Adding User Login + Verified Email Connections to an Existing FastAPI Application

## 1. Objective

This document describes an extension to an **existing FastAPI application** that adds:

1. A user login/registration system.
2. A verified-email connection system.
3. The ability for a user to enter an email address such as:
   - `user@gmail.com`
   - `user@outlook.com`
   - `username@your-domain.com`
4. A check to determine whether that email is already authenticated/connected to the current application user.
5. If it is not connected, the application asks the user to authenticate that mailbox through the appropriate email provider.
6. A strict rule that users can access **only their own connected/verified email records**.
7. Support for both consumer email providers and custom-domain mailboxes.

The recommended architecture uses **OAuth 2.0 / OpenID Connect where the provider supports it**, plus provider-specific APIs. SMTP/IMAP credentials should not be collected by the application unless there is a specific legacy requirement.

---

# 2. Important distinction: verifying an email vs. accessing a mailbox

There are two different requirements that are often confused.

### A. Email ownership verification

The application sends a one-time verification link/code to:

`username@your-domain.com`

The user clicks the link and proves that they control the address.

This does **not** give the application permission to read email.

### B. Mailbox authentication / connection

The user explicitly connects the mailbox using the provider's authentication flow.

For example:

```text
Your application
      |
      | "Connect Gmail"
      v
Google OAuth
      |
      | user signs in
      | grants requested scopes
      v
Google
      |
      | authorization code
      v
FastAPI backend
      |
      | exchanges code for tokens
      v
Encrypted token storage
```

This gives the application delegated access to whatever mailbox permissions the user approved.

If the requirement is to determine whether an address belongs to a mailbox/account and then potentially access that mailbox, **OAuth is the appropriate design**.

---

# 3. Recommended high-level architecture

Use the existing FastAPI application and add an authentication/identity layer.

```text
                         ┌──────────────────────┐
                         │ Existing Frontend    │
                         │ Login / Dashboard    │
                         └──────────┬───────────┘
                                    │ HTTPS
                                    v
                         ┌──────────────────────┐
                         │ Existing FastAPI     │
                         │ Application          │
                         ├──────────────────────┤
                         │ Auth API             │
                         │ Email API            │
                         │ OAuth callbacks      │
                         │ Authorization       │
                         └──────────┬───────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
                v                   v                   v
        ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
        │ PostgreSQL   │    │ Token Store  │    │ Email        │
        │ Users        │    │ Encrypted    │    │ Providers    │
        │ Emails       │    │ OAuth tokens │    │              │
        └──────────────┘    └──────────────┘    └──────────────┘
                                                     │
                                      ┌──────────────┼──────────────┐
                                      │              │              │
                                    Google       Microsoft      Custom Domain
                                    OAuth         OAuth          OAuth/IMAP
```

---

# 4. Recommended technology stack

For an existing FastAPI application:

| Component | Recommendation |
|---|---|
| API | FastAPI |
| Database | PostgreSQL |
| ORM | SQLAlchemy 2.x |
| Database migrations | Alembic |
| Password hashing | Argon2id via `pwdlib` |
| Session/JWT authentication | Secure HTTP-only cookie sessions or short-lived JWT + refresh token |
| OAuth | Authlib |
| Validation | Pydantic v2 |
| HTTP client | httpx |
| Encryption | `cryptography` |
| Password reset | Signed, short-lived tokens |
| Email verification | Signed, short-lived tokens |
| Production TLS | HTTPS |
| Background jobs | Celery/RQ/Arq depending on the existing architecture |

`Authlib` is a strong choice for OAuth 2.0 and OpenID Connect integrations.

---

# 5. Database design

Do not put all connected emails into the `users` table.

Use separate tables so one application user can connect multiple email accounts.

## users

```text
users
-----
id
email
password_hash
is_active
created_at
updated_at
```

The `email` here should be the application's login/primary identity, not necessarily every mailbox the user connects.

## email_accounts

```text
email_accounts
--------------
id
user_id
email_address
provider
provider_account_id
is_verified
is_active
created_at
updated_at
last_authenticated_at
```

Example:

```text
id: 42
user_id: 7
email_address: username@your-domain.com
provider: microsoft
provider_account_id: AABBCC123
is_verified: true
```

## oauth_credentials

Keep OAuth credentials separate from ordinary email records.

```text
oauth_credentials
-----------------
id
email_account_id
access_token_encrypted
refresh_token_encrypted
expires_at
scopes
created_at
updated_at
```

The token fields must be encrypted at rest.

---

# 6. Critical authorization rule

Every email-account query must be scoped to the authenticated application user.

Never do this:

```python
email_account = db.query(EmailAccount).filter(
    EmailAccount.id == email_account_id
).first()
```

because a user could potentially change:

```text
/email-accounts/42
```

to:

```text
/email-accounts/43
```

and access somebody else's account.

Instead:

```python
email_account = db.query(EmailAccount).filter(
    EmailAccount.id == email_account_id,
    EmailAccount.user_id == current_user.id
).first()
```

The same rule must apply to:

- GET
- POST
- PATCH
- DELETE
- OAuth callback processing
- token refresh
- mailbox synchronization
- background jobs

Authorization must be enforced server-side.

---

# 7. Login flow

A normal login flow can be:

```text
POST /auth/register
        |
        v
Create user
        |
        v
Hash password with Argon2id
        |
        v
Send email verification
```

Then:

```text
POST /auth/login
        |
        v
Validate credentials
        |
        v
Create authenticated session
        |
        v
Set Secure + HttpOnly cookie
```

The frontend can then call:

```text
GET /auth/me
```

to retrieve the logged-in user's application identity.

---

# 8. Connecting an email address

Suppose the logged-in user enters:

```text
username@your-domain.com
```

Frontend:

```http
POST /email-accounts/connect
Content-Type: application/json

{
    "email": "username@your-domain.com"
}
```

FastAPI should:

1. Authenticate the application user.
2. Normalize the address.
3. Determine the likely provider.
4. Check whether the address is already connected to **this user**.
5. If already connected, return its status.
6. If not connected, start an authentication flow.

Example response:

```json
{
  "status": "authentication_required",
  "provider": "microsoft",
  "authorization_url": "..."
}
```

The frontend redirects the user to the provider.

---

# 9. Why the application should not simply trust the typed email

A user typing:

```text
ceo@company.com
```

does not prove that the user controls that mailbox.

The application should never treat the input itself as authentication.

The provider authentication result should establish the identity.

For OAuth/OIDC providers, the callback should validate the provider-issued identity information and obtain the provider's stable account identifier.

---

# 10. Gmail / Google Workspace

For Google-managed accounts:

```text
User
 |
 | Connect Google email
 v
Google OAuth
 |
 | authorization code
 v
FastAPI
 |
 | exchange code for tokens
 v
Google API
```

The application should request the **minimum scopes required**.

For example, if the application only needs identity information, request identity scopes.

If it needs to read mail, request the appropriate Gmail scope separately.

Do not request full mailbox access merely to determine who authenticated.

Google Workspace custom-domain addresses such as:

```text
alice@company.com
```

can still be Google-managed. The domain itself does not determine the provider.

---

# 11. Microsoft / Microsoft 365

For Microsoft-hosted mailboxes, use Microsoft identity/OAuth.

This covers addresses such as:

```text
user@outlook.com
user@hotmail.com
employee@company.com
```

where the mailbox is backed by Microsoft services.

Again, request only the permissions required by the application.

For Microsoft 365 organizations, administrator consent may be required for certain permissions.

---

# 12. Custom-domain email

This is the important part for:

```text
username@your-domain.com
```

A custom domain does not automatically mean a particular mail provider.

For example, the domain could be hosted by:

- Google Workspace
- Microsoft 365
- Zoho
- Fastmail
- a private mail server
- another hosted provider

The application can inspect the domain's DNS records to identify likely mail infrastructure.

Most importantly:

## MX records do NOT authenticate the user.

An MX record only tells you where mail for the domain is delivered.

For example:

```text
your-domain.com
        |
        +-- MX --> mail.provider.example
```

This can help discover the provider, but it cannot prove that:

```text
username@your-domain.com
```

belongs to the current user.

---

# 13. Provider discovery

A practical architecture is:

```text
email address
     |
     v
extract domain
     |
     v
DNS / configured provider mapping
     |
     +---- Google Workspace?
     |
     +---- Microsoft 365?
     |
     +---- Known OAuth provider?
     |
     +---- Unknown/custom mail server?
```

For known providers, launch OAuth.

For unknown providers, there are two possibilities:

### Option 1 — Email ownership verification

Send a verification message.

This is the simplest and safest fallback.

### Option 2 — IMAP/SMTP authentication

Some private/custom mail systems expose IMAP.

The user could provide credentials through a secure provider-specific flow.

However, this is substantially more complicated than OAuth and should not be the default.

Do not store plaintext IMAP passwords.

---

# 14. OAuth callback security

A typical flow:

```text
GET /oauth/{provider}/start
        |
        v
Generate state + PKCE
        |
        v
Redirect to provider
        |
        v
User authenticates
        |
        v
GET /oauth/{provider}/callback
        |
        v
Validate state
        |
        v
Exchange authorization code
        |
        v
Validate identity
        |
        v
Compare authenticated email
        |
        v
Create/update email_accounts
```

The `state` parameter protects against CSRF.

PKCE should be used where supported.

Never accept an OAuth callback without validating the expected state/session relationship.

---

# 15. Very important: prevent account linking attacks

Suppose Alice is logged into your application.

Alice enters:

```text
bob@company.com
```

and somehow obtains an OAuth authorization for Bob's mailbox.

Your backend must not blindly associate that mailbox with Alice.

At the callback:

```python
authenticated_provider_email = ...
authenticated_provider_account_id = ...
requested_email = ...
current_user = ...
```

Validate that the authenticated provider identity corresponds to the account the user intended to connect.

If there is a mismatch:

```text
Requested:
bob@company.com

Authenticated:
alice@company.com
```

stop the connection and require explicit correction.

---

# 16. Suggested API endpoints

Add an authentication namespace:

```text
POST   /auth/register
POST   /auth/login
POST   /auth/logout
GET    /auth/me
POST   /auth/verify-email
POST   /auth/forgot-password
POST   /auth/reset-password
```

Add email-account endpoints:

```text
POST   /email-accounts/connect
GET    /email-accounts
GET    /email-accounts/{id}
DELETE /email-accounts/{id}
```

Provider endpoints:

```text
GET /oauth/google/start
GET /oauth/google/callback

GET /oauth/microsoft/start
GET /oauth/microsoft/callback
```

If the frontend requires the authorization URL as JSON rather than an HTTP redirect:

```text
POST /email-accounts/connect
```

can return:

```json
{
  "provider": "google",
  "requires_authentication": true,
  "authorization_url": "..."
}
```

---

# 17. Example FastAPI dependency

A central dependency should identify the application user.

Conceptually:

```python
async def get_current_user(
    session: AsyncSession = Depends(get_db),
    token: str = Depends(get_session_token),
):
    user = await authenticate_session(session, token)

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    return user
```

Then every private endpoint uses:

```python
@router.get("/email-accounts")
async def list_email_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ...
```

---

# 18. Example ownership query

```python
result = await db.execute(
    select(EmailAccount).where(
        EmailAccount.id == account_id,
        EmailAccount.user_id == current_user.id,
    )
)

email_account = result.scalar_one_or_none()

if email_account is None:
    raise HTTPException(
        status_code=404,
        detail="Email account not found",
    )
```

This pattern should be used throughout the application.

A `403` can also be used when an object exists but is intentionally hidden. A common security-oriented approach is returning `404` for resources that do not belong to the user so that their existence is not disclosed.

---

# 19. "Already authenticated?" check

When the user enters an email:

```text
username@your-domain.com
```

the backend should check:

```sql
SELECT *
FROM email_accounts
WHERE user_id = :current_user_id
  AND normalized_email = :normalized_email
  AND is_active = true;
```

If found:

```json
{
  "connected": true,
  "verified": true
}
```

If not:

```json
{
  "connected": false,
  "requires_authentication": true
}
```

This check is scoped to the logged-in user.

---

# 20. What if the same email is already connected to another application user?

This should generally be treated as an account-linking conflict.

For example:

```text
Application user A
        |
        +-- alice@company.com
```

User B attempts:

```text
Connect alice@company.com
```

The application should not automatically transfer the email account.

Recommended behavior:

```text
This email is already associated with another application account.
```

Do not reveal unnecessary information about the other account.

An administrative account-recovery/link-transfer workflow can be implemented separately if required.

---

# 21. Database constraints

Add a uniqueness constraint.

A useful design is:

```text
UNIQUE(user_id, normalized_email)
```

This prevents the same user from accidentally creating duplicate records.

Depending on the business rules, you may also enforce:

```text
UNIQUE(provider, provider_account_id)
```

This prevents the same provider account from being attached to multiple application users.

That second constraint is particularly useful for preventing accidental or malicious account linking.

---

# 22. Email normalization

At minimum:

```python
email = email.strip().lower()
```

Store the normalized address.

However, do not implement provider-specific transformations such as Gmail dot removal or plus-address stripping unless you have a deliberate reason. Those rules are provider-specific and can cause account-collision bugs.

Use the provider's authenticated account identifier as the authoritative identity where available.

---

# 23. Token storage

Never store OAuth tokens like this:

```text
access_token = "plain-text-token"
```

Use authenticated encryption.

For example, an application-managed encryption key can encrypt the token before database storage.

Conceptually:

```python
encrypted_access_token = encrypt(access_token)
encrypted_refresh_token = encrypt(refresh_token)
```

The encryption key should be supplied through a secrets-management mechanism/environment configuration, not committed to Git.

For production deployments, consider a dedicated KMS/secrets manager.

---

# 24. Access tokens and refresh tokens

Access tokens are usually short-lived.

The application may receive:

```text
access_token
expires_at
refresh_token
```

When an access token expires:

```text
FastAPI
   |
   +-- refresh token
   |
   v
Provider
   |
   v
new access token
```

Refresh-token handling must be provider-specific.

If a provider rotates refresh tokens, replace the stored refresh token atomically.

---

# 25. Do not use OAuth merely to check whether an email exists

There is an important privacy/security consideration.

You should not implement:

```text
POST /check-email
```

that takes arbitrary addresses and attempts to determine whether those addresses correspond to real accounts.

Instead:

1. User logs into your application.
2. User explicitly chooses "Connect email".
3. User enters an address.
4. Application starts an authentication flow.
5. Provider authentication establishes the identity.

This avoids creating an email-account enumeration service.

---

# 26. Recommended frontend UX

The UI can be:

```text
Email accounts

+-----------------------------------------+
| Add an email account                    |
|                                         |
| Email                                   |
| [ username@your-domain.com          ]   |
|                                         |
| [ Connect email ]                       |
+-----------------------------------------+

Connected accounts

✓ username@your-domain.com
  Microsoft 365
  Authenticated
  [Disconnect]

✓ user@gmail.com
  Google
  Authenticated
  [Disconnect]
```

When the email is not connected:

```text
This email has not been connected yet.

To verify and connect it, you'll be redirected
to your email provider.

[Authenticate email]
```

---

# 27. Separate application login from connected email accounts

This distinction is strongly recommended.

The application account:

```text
User
  |
  +-- username/password or SSO
```

is not the same object as:

```text
Connected Email Account
  |
  +-- Google
  +-- Microsoft
  +-- Custom provider
```

One user may have:

```text
Application User #123

    ├── personal@gmail.com
    ├── work@company.com
    └── secondary@company.com
```

All three are owned by the same application user.

---

# 28. Suggested project structure

For an existing FastAPI application, a possible addition is:

```text
app/
├── main.py
├── config.py
│
├── auth/
│   ├── router.py
│   ├── service.py
│   ├── dependencies.py
│   ├── schemas.py
│   └── security.py
│
├── email_accounts/
│   ├── router.py
│   ├── service.py
│   ├── schemas.py
│   ├── models.py
│   └── providers/
│       ├── google.py
│       ├── microsoft.py
│       └── custom.py
│
├── oauth/
│   ├── service.py
│   ├── state.py
│   └── token_store.py
│
├── models/
│   ├── user.py
│   ├── email_account.py
│   └── oauth_credential.py
│
└── db/
    ├── session.py
    └── migrations/
```

The exact structure should be adapted to the existing application's architecture rather than replacing it.

---

# 29. Python libraries

Useful libraries include:

```text
fastapi
sqlalchemy
alembic
pydantic
authlib
httpx
cryptography
pwdlib
email-validator
```

Potentially:

```text
redis
```

for short-lived OAuth state/session storage.

For asynchronous FastAPI applications, use the async versions/patterns supported by the existing database and HTTP stack.

---

# 30. OAuth provider abstraction

Do not hard-code provider logic into the main endpoint.

Create a provider interface such as:

```python
class EmailProvider:
    async def get_authorization_url(self, ...):
        ...

    async def exchange_code(self, ...):
        ...

    async def get_identity(self, ...):
        ...

    async def refresh_token(self, ...):
        ...
```

Then implement:

```text
GoogleProvider
MicrosoftProvider
CustomProvider
```

The main application can operate against the interface.

This makes adding another provider much easier.

---

# 31. Custom provider strategy

For arbitrary:

```text
username@your-domain.com
```

the application should maintain a provider-discovery strategy.

For example:

```text
domain
  |
  +-- known configured domain?
  |       |
  |       +-- use configured provider
  |
  +-- DNS discovery
  |
  +-- known provider match?
  |
  +-- fallback to email verification
```

For domains you control, an even better option is an explicit configuration table:

```text
email_domains
-------------
domain
provider
oauth_issuer
oauth_client_id
enabled
```

Example:

```text
company.com -> microsoft
your-domain.com -> google
```

This is more reliable than trying to infer the provider solely from DNS.

---

# 32. If the custom domain uses your own mail server

If your application controls the mail infrastructure, you can provide a dedicated authentication mechanism.

For example:

```text
username@your-domain.com
        |
        v
Your Identity Provider
        |
        v
OAuth/OIDC
        |
        v
FastAPI
```

This is preferable to giving the application raw mailbox passwords.

If the mail server only exposes IMAP, a dedicated authentication service or secure credential broker is preferable to having every application component directly handle IMAP passwords.

---

# 33. Security requirements

At minimum implement:

- HTTPS everywhere.
- Secure, HttpOnly cookies for browser sessions.
- SameSite protection appropriate to the OAuth flow.
- CSRF protection where applicable.
- OAuth `state` validation.
- PKCE.
- Short-lived authorization state.
- Short-lived password-reset tokens.
- Short-lived email-verification tokens.
- Argon2id password hashing.
- Encrypted OAuth tokens at rest.
- Secret management outside Git.
- Rate limiting on login and verification endpoints.
- Login failure monitoring.
- Audit logs for account linking/unlinking.
- Strict user ownership checks.
- Minimal OAuth scopes.
- Token revocation/disconnection handling.

---

# 34. Audit logging

Account linking is a security-sensitive operation.

Record events such as:

```text
USER_LOGIN
EMAIL_CONNECTION_STARTED
EMAIL_CONNECTION_COMPLETED
EMAIL_CONNECTION_FAILED
EMAIL_DISCONNECTED
OAUTH_TOKEN_REFRESHED
PASSWORD_CHANGED
```

Do not put access tokens, refresh tokens, passwords, or authorization codes into logs.

---

# 35. Recommended implementation sequence

Because this is an **addition to an existing FastAPI application**, implement it incrementally.

### Phase 1 — Application authentication

Add:

```text
users
sessions
```

and:

```text
/register
/login
/logout
/me
```

### Phase 2 — Email verification

Add:

```text
email verification tokens
```

and verify the application's primary email.

### Phase 3 — Connected email accounts

Add:

```text
email_accounts
oauth_credentials
```

### Phase 4 — Google OAuth

Implement:

```text
GoogleProvider
```

and test the complete connect/disconnect lifecycle.

### Phase 5 — Microsoft OAuth

Implement:

```text
MicrosoftProvider
```

### Phase 6 — Custom domains

Implement:

```text
domain discovery
configured domain mappings
email verification fallback
```

### Phase 7 — Mailbox APIs

Only after identity/linking works should you add actual mailbox operations such as:

```text
read messages
search messages
send messages
```

Each of those should use the provider's API and the stored delegated authorization.

---

# 36. Testing plan

Test at least these cases:

### Authentication

```text
✓ Register
✓ Login
✓ Logout
✓ Invalid password
✓ Expired session
✓ Password reset
```

### Email verification

```text
✓ Valid verification token
✓ Expired verification token
✓ Already-used token
✓ Invalid token
```

### Connected email

```text
✓ Connect new email
✓ Connect same email twice
✓ Disconnect email
✓ Connect multiple emails
✓ User A cannot access User B's email
✓ User A cannot delete User B's email
```

### OAuth

```text
✓ Valid state
✓ Invalid state
✓ Expired state
✓ Callback with wrong user/session
✓ Provider identity mismatch
✓ Expired access token
✓ Refresh token rotation
✓ Provider revokes authorization
```

### Custom domains

```text
✓ Google Workspace custom domain
✓ Microsoft 365 custom domain
✓ Unknown provider
✓ Private mail server
✓ MX records unavailable
```

---

# 37. Recommended final architecture

The cleanest architecture for the existing FastAPI application is:

```text
                    ┌────────────────────┐
                    │ Application User   │
                    │ Login              │
                    └─────────┬──────────┘
                              │
                              v
                    ┌────────────────────┐
                    │ FastAPI Auth       │
                    │ Session / JWT      │
                    └─────────┬──────────┘
                              │
                              v
                    ┌────────────────────┐
                    │ Current User ID    │
                    └─────────┬──────────┘
                              │
              ┌───────────────┴────────────────┐
              │                                │
              v                                v
      ┌─────────────────┐             ┌─────────────────┐
      │ Email Accounts  │             │ OAuth Tokens    │
      │ owned by user   │             │ encrypted       │
      └────────┬────────┘             └────────┬────────┘
               │                               │
               └──────────────┬────────────────┘
                              │
                              v
                     ┌─────────────────┐
                     │ Email Provider  │
                     ├─────────────────┤
                     │ Google          │
                     │ Microsoft       │
                     │ Custom          │
                     └─────────────────┘
```

The central security invariant is:

```text
Authenticated Application User
        +
        +
Email Account.user_id == current_user.id
        =
Permission to access the connected email record
```

Provider authentication establishes the external mailbox identity; your application's authorization layer determines which application user is allowed to access the resulting connection.

---

# 38. Bottom line

Yes, this can be implemented cleanly with FastAPI.

The key technologies are:

```text
FastAPI
+ PostgreSQL
+ SQLAlchemy
+ Authlib
+ OAuth 2.0 / OpenID Connect
+ encrypted token storage
+ application-level sessions
```

The recommended flow is **not**:

```text
user enters email
→ server secretly checks whether mailbox exists
```

Instead use:

```text
User logs into application
        ↓
User enters email address
        ↓
Application checks whether that address is already
connected to THIS application user
        ↓
If connected → show authenticated/connected
        ↓
If not connected → start provider authentication
        ↓
Provider authenticates the mailbox/account
        ↓
FastAPI validates the callback and identity
        ↓
Store connection against current user
        ↓
Encrypt and store OAuth credentials
        ↓
User can access only their own connected emails
```

For custom domains, treat the domain as **provider-discovery information, not proof of identity**. MX/DNS records can help identify where mail is hosted, but the authenticated provider response or an email-verification challenge must establish control of the address.

This architecture can be added to an existing FastAPI application without replacing its current business logic; the new authentication, email-account, OAuth-provider, database, and authorization components should be integrated around the existing routes/services.
