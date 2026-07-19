# Users & Authentication

The **Users** system manages user accounts, roles, and access permissions.

## User Roles

| Role | Permissions |
|------|-------------|
| **Admin** | Full access, including settings and user management |
| **Senior Investigator** | All features except settings and user management |
| **Investigator** | Standard investigator: cases, subjects, findings |
| **Viewer** | Read only: can view data but not edit |

## Logging In

1. Go to the login screen at `/auth/login`
2. Enter username and password
3. Click **Login**

### Two-Factor Authentication (2FA)

Users can enable 2FA via a TOTP app (Google Authenticator, Authy, etc.):

1. Go to your profile page
2. Click **Enable 2FA**
3. Scan the QR code with your authenticator app
4. Enter a one-time code to verify

A ✓ icon next to your name in the header indicates that 2FA is active.

## Forgot Password

1. Click **Forgot Password** on the login screen
2. Enter your email address
3. Receive a reset link (valid for 48 hours)
4. Click the link and choose a new password (minimum 8 characters)

Note: passwords are **never** sent via email.

## Creating a User (Admin)

1. Go to **Settings** → **Users**
2. Click **Create User**
3. Fill in: username, email, full name, role
4. Optionally: send a "Set Password" email
5. The system generates a temporary password that is only shown on screen

## Editing Your Profile

Click your name in the header to view/edit your profile:

- Change your full name
- Change your email address
- Change your password
- Enable/disable 2FA

## Security

- **Password requirements**: minimum 8 characters
- **Session expires**: after 8 hours of inactivity
- **Rate limiting**: 30 create/edit actions per 60 seconds
- **Failed login attempts**: account is temporarily locked after multiple failed attempts
- **HTTP-only cookies**: session cookies are not accessible via JavaScript
- **Security headers**: X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, HSTS are sent in responses
