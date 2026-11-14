# Free Fire top-up backend + admin panel

    cd backend && pip install -r requirements.txt
    cp .env.example .env   # fill in, then export the variables
    uvicorn app.main:app --port 8000
    # Admin panel: http://localhost:8000/admin/   API docs: /docs

Customer API (all under /api): auth/register, auth/login, me, packages (public, no package numbers),
check-player, orders (POST/GET), wallet/topups (POST/GET), codes.
Admin API (under /api/admin, admin role only): packages CRUD by package number, orders + status,
users + balance adjust, add-money requests approve/reject.
Run behind HTTPS (e.g. Caddy/Nginx). Not yet included: user-facing frontend, rate limiting, payment-gateway webhook.
