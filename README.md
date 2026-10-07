# Lemco Portal

A Frappe app that gives Lemco customers a self-service portal for support, repairs, projects, subscriptions and webinars, built on top of Helpdesk and LMS.

## Purpose

Lemco customers need one place to manage their relationship with the company. This app provides that portal, with staff approval before any new customer gets access. Customer pages are built on Frappe's Website/Portal framework (not the Desk UI), so customers never see the back office.

## Use cases

- A new company signs up and waits for Lemco to approve its account.
- A customer logs in, sees open tickets, projects, subscriptions and repairs at a glance, and jumps to Helpdesk, the knowledge base or the Academy.
- A customer registers for a Microsoft Teams webinar.
- A customer submits a repair (RMA) request, tracks it end to end, and approves chargeable repairs online.
- A company's manager adds teammates and controls who sees what.
- Lemco staff approve customers and run the repair workflow from Desk.

## Features

- **Signup with approval gate:** creates Customer, Address, Contact and User; login is blocked until `Portal Access = Yes`.
- **Dashboard:** stat cards, quick links, upcoming webinars and recent activity.
- **Projects and subscriptions:** list and create from the portal.
- **Org users:** managers add teammates; members see only their own projects and subscriptions.
- **Webinars:** hourly sync from Microsoft Teams (Graph API) and one-click registration.
- **Repair Service (RMA):** portal request form, status tracking, customer approvals, staff workflow, QR-coded print formats, final PDF report and email notifications.
- **Password reset:** branded flow with hashed reset keys.

## Requirements

- Frappe / ERPNext
- Apps: `helpdesk`, `lms`, `crm`
- Python >= 3.10

## Installation

```bash
cd frappe-bench
bench get-app lemco_portal https://github.com/<your-username>/lemco_portal.git
bench --site your-site.local install-app lemco_portal
bench --site your-site.local migrate
bench build
```

On Frappe Cloud: Bench → Apps → Install from GitHub, deploy, then install on the site.

## Configuration

1. Set up the outgoing **Email Account** in Desk.
2. Enter the Entra **Tenant ID, Client ID and Client Secret** in *Lemco Portal Settings* (needs `VirtualEvent.Read.All` and `VirtualEventRegistration-Anon.ReadWrite.All`, plus a Teams Application Access Policy for each webinar organizer).
3. Fill in **RMA Settings** and tick **Allow RMA** on the Items customers may send for repair.
4. Run the one-time layout setup:
   ```bash
   bench --site your-site.local execute lemco_portal.api.setup_backend_layout
   bench --site your-site.local execute lemco_portal.api.setup_lemco_letter_head
   ```
5. Approve each new customer by setting **Portal Access** to **Yes** on the Customer record.

## Portal pages

`/login` · `/create-account` · `/reset-password` · `/dashboard` · `/projects` · `/subscriptions` · `/users` · `/webinars` · `/repairs`

## License

MIT
