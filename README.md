# Lemco Portal

A Frappe app implementing the **Customer Portal** milestone from the Lemco
brief: signup with approval gating, a customer dashboard, and a webinars
page — built on Frappe's **Website/Portal** framework (not the Desk UI),
per the client's instruction to stick to standard Frappe functionality.

## What's included

| Page | Route | Purpose |
|---|---|---|
| Login | `/login` | Custom-styled login form + "Create account" link |
| Create account | `/create-account` | Signup form matching the client's field table exactly |
| Dashboard | `/dashboard` | Customer home — stat cards, quick access, webinars, recent activity |
| Webinars | `/webinars` | Full upcoming-webinar list with Register buttons |

Plus:
- **`Webinar`** doctype — staff create these (title, date, time, language,
  speaker, Teams join URL) under Desk.
- **`Webinar Registration`** doctype — one record per customer sign-up.
- **`Customer.portal_access`** custom field (Select: No/Yes, default No) —
  added automatically on install via `after_install`.
- **Login gate** (`on_session_creation` hook) — logs a portal customer back
  out immediately if their linked Customer's `portal_access` isn't "Yes",
  and redirects to `/login?pending=1`.

## Signup flow (matches the brief's field table)

`POST /api/method/lemco_portal.api.create_customer_account` creates, in order:

1. **Customer** — `customer_name`, `tax_id`, `website`, `customer_type = Company`, `portal_access = No`
2. **Address** — marked `is_primary_address` + `is_shipping_address`, type `Billing`, linked to the Customer
3. **Contact** — set as `customer_primary_contact`, linked to the Customer
4. **User** (Website User, role `Customer`) — so they *can* eventually log in
5. Confirmation email via `frappe.sendmail`

The account is created immediately, but can't be used to log in until a
Lemco employee flips **Portal Access** to **Yes** on the Customer record in
Desk (`/app/customer/<name>`) — enforced server-side by the login hook, not
just hidden in the UI.

## Dashboard link mapping (as specified by the client)

| Card / link | Target |
|---|---|
| My Tickets | `/helpdesk/tickets` |
| My Services | `/desk/project?status=Open` |
| My Projects | `/desk/project?status=Open` |
| My Subscriptions | `/desk/subscription/view/report` |
| Knowledge base | `/helpdesk/kb` |
| Academy | `/lms/` |
| Users | `/app/contact` *(see open question below)* |
| Lemco website (top right) | `https://www.lemco.gr` (new tab) |
| Fleex website (top right) | `https://www.fleex.gr` (new tab) |
| Create a ticket | `/helpdesk/tickets/new` |

Stat cards are filtered to the logged-in customer via
`lemco_portal.api.get_dashboard_data`.



## Webinars + Microsoft Teams — now fully wired up

Your Entra app registration has both permissions needed, granted and
consented:

- `VirtualEvent.Read.All` (Application) — lists webinars
- `VirtualEventRegistration-Anon.ReadWrite.All` (Application) — registers customers

### How it works

- **`lemco_portal/graph.py`** authenticates via the client-credentials flow
  using credentials you enter in **Lemco Portal Settings**
  (`/app/lemco-portal-settings` — Tenant ID, Client ID, Client Secret; the
  secret is stored encrypted).
- An **hourly scheduled job** (`lemco_portal.graph.sync_webinars`) calls
  `GET /solutions/virtualEvents/webinars` and upserts each one into the
  local `Webinar` doctype, matched on the new `teams_webinar_id` field.
  This keeps `/webinars` fast and resilient even if Graph is briefly down.
- When a customer clicks **Register** on `/webinars`, `register_for_webinar`
  checks whether the webinar has a `teams_webinar_id`:
  - **If yes** (synced from Teams) → calls
    `POST /solutions/virtualEvents/webinars/{id}/registrations` with the
    customer's name/email. **Teams itself sends the registration and
    reminder emails** — this is what the `-Anon` permission is for. The
    personalized join link Teams returns is stored on the
    `Webinar Registration` record.
  - **If no** (a webinar someone entered by hand, with just a pasted-in
    `teams_join_url`) → falls back to the old behavior: ERPNext sends its
    own confirmation email with that link.

### Setup you still need to do (not code, this is your Azure/Teams config)

1. Fill in **Lemco Portal Settings** with the Tenant ID, Client ID, and
   Client Secret from the app registration.
2. `GET /solutions/virtualEvents/webinars` **only returns webinars whose
   organizer has an Application Access Policy** for this app — same
   requirement as before. With Teams admin PowerShell:
   ```powershell
   New-CsApplicationAccessPolicy -Identity "LemcoPortalWebinarAccess" -AppIds "b7d85da3-8a70-4e16-8894-def84c6ebaea" -Description "Lemco Portal webinar sync/registration"
   Grant-CsApplicationAccessPolicy -PolicyName "LemcoPortalWebinarAccess" -Identity "organizer@lemco.gr"
   ```
   Repeat the `Grant-CsApplicationAccessPolicy` line for each employee who'll organize webinars.
3. Each organizer needs a webinar (not a plain meeting) created in Teams —
   this is a distinct event type in Teams, with its own registration page.
4. Once policies are in place, either wait for the hourly job or run
   `frappe.call("lemco_portal.graph.sync_webinars_now")` from the Desk
   console (System Manager only) to pull webinars immediately for testing.

### Still worth knowing

- Listing webinar **attendees/registrants** back out isn't available in
  this Graph API yet (Microsoft's docs note this explicitly) — so there's
  no way to build an "attendee report" inside ERPNext beyond what's already
  tracked in the local `Webinar Registration` doctype from registrations
  made *through the portal*. Registrations made directly in Teams by people
  outside the portal won't show up in ERPNext.
- Webinars require the organizer to hold an M365 E3/E5 or Teams Premium
  license — a Teams/licensing detail, not something this app controls.

## Installation

### On a bench (self-hosted)

```bash
cd frappe-bench
bench get-app lemco_portal https://github.com/<your-username>/lemco_portal.git
bench --site your-site.local install-app lemco_portal
bench --site your-site.local migrate
bench build
```

### On Frappe Cloud

1. Push this repo to GitHub.
2. Bench → **Apps** → **Install App** → **Install from GitHub** → this repo's URL.
3. Deploy the bench, then install the app on `portal.lemco.gr` from the
   site's **Apps** tab.
4. In Desk, go to **Website Settings** (or set `home_page` per role) so
   Guests land on `/login` and Website Users land on `/dashboard` — the
   `role_home_page` hook here already routes the `Customer` role to
   `/dashboard`.

## After install — manual setup steps

- Add at least one `Webinar` record (`/app/webinar/new`) so `/webinars` has
  something to show.
- For each new signup, go to `/app/customer/<name>` and set **Portal
  Access** to **Yes** once verified.
- Configure the outgoing Email Account in Desk (Settings → Email Account)
  once Mailcow/no-reply@lemco.gr is ready — no code changes needed here.
- `required_apps = ["helpdesk", "lms", "crm"]` in `hooks.py` tells
  bench/Frappe Cloud to ensure those are installed first.

## Known limitations / things to verify on your actual site

- `HD Ticket`, `Project`, `Subscription` filters in `get_dashboard_data()`
  assume a `customer` (or `party`) field links each record to the
  Customer — confirm those field names match your installs (Helpdesk in
  particular sometimes links tickets via `customer` on `HD Ticket`, verify
  it's enabled in Helpdesk settings).
- Overriding the core `/login` page by shipping our own `www/login/index.html`
  is a standard Frappe technique, but page resolution/precedence has
  shifted a little between Frappe versions — test this first on staging.
- CSRF: form posts use `frappe.csrf_token`, which is only present once the
  base website template has loaded it — confirmed present in the standard
  `templates/web.html` base template these pages extend.

## License

MIT
