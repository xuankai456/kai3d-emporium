# Kai's Emporium

A Flask and SQLite portfolio and asset vault for Kai's 3D art. The app includes
account sign-in, a public-facing portfolio, downloadable vault assets, and an
admin-only studio dashboard for managing content.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"
flask --app app create-admin alqaqa469@gmail.com
flask --app app run
```

Open `http://127.0.0.1:5000`. The first page lets visitors sign in or create an
account using a username and password; no email or verification is required.
The `create-admin` command prompts for the admin password and stores only its
secure hash. Sign in with the configured admin email to access the Studio
dashboard.

The local SQLite database is created under `instance/`. Uploaded project files
are stored in `static/uploads/`; both locations are excluded from Git. Portfolio
and vault files can be uploaded through the admin dashboard after creating the
admin account. Uploads accept any file format up to 100 MB, while preview images
must be JPG, PNG, or WebP.

For deployment, configure a persistent random `SECRET_KEY`, enable HTTPS and
`COOKIE_SECURE=1`, and use a production WSGI server. Set `CONTACT_EMAIL` to the
studio's preferred address before publishing.

## Deploy to Vercel

Import this repository into Vercel and deploy with the default Python runtime.
Vercel detects the Flask `app` exported from the root `app.py`. The root
`vercel.json` includes `templates/**` and `static/**` in that function bundle so
Flask can render its Jinja templates and serve the app's static assets.

Set `SECRET_KEY`, `COOKIE_SECURE=1`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD` in
Vercel's environment variables. When both admin variables are configured, the
app creates or updates the admin account at startup and stores only a password
hash in SQLite. The configured admin username is reserved from public signup.
Vercel's `/tmp` storage is temporary and may be reset between function
instances, so this SQLite-backed app is suitable for a preview deployment
only; accounts, uploaded files, and edits are not durable there. Before using
it as a live site, move the database to a persistent hosted database and
uploads to persistent object storage.
