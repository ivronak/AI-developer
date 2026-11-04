"""Demo data — `flask seed`. Mirrors the mock's mock-ui/assets/js/data.js (same generic projects, users and changes)
so the real pages can be compared with the mock side by side. Refuses to run on a database that already has users."""
import hashlib
import secrets
from datetime import timedelta

import click

from app.clock import ago
from app.crypto import sha256
from app.extensions import db
from app.models import (ActivityLog, Alert, Approval, Backup, Change, ChangeMessage, Deployment, Environment, Project,
                        ProjectMember, User)

DEMO_PASSWORD = "demo1234"

USERS = [
    # name, email, mobile, role, project ids, active, last sign-in (min ago)
    ("Owner", "owner@demo.test", "+91 98765 44821", "owner", [], True, 2),
    ("Developer 1", "dev@demo.test", "+91 98765 11203", "developer", [2, 5, 7], True, 180),
    ("Developer 2", "dev2@demo.test", "+91 98765 33310", "developer", [4, 12], True, 1500),
    ("Developer 3", "dev3@demo.test", "+91 98765 70045", "developer", [3], False, 20160),
]

PROJECTS = [
    # id, name, client, tech, host, active, incident, last deploy (min ago), last backup (min ago), failed backups
    (1, "Parking Manager", "Client A", ["Laravel", "Flutter"], "vps", True, "Live site down for 12 min", 140, 300, 0),
    (2, "Clinic Booking", "Client B", ["Laravel"], "vps", True, "", 1500, 240, 0),
    (3, "School ERP", "Client C", ["Laravel", "Flutter"], "vps", True, "", 4320, 3000, 2),
    (4, "Food Delivery", "Client D", ["Laravel", "Flutter"], "cpanel", True, "", 600, 420, 0),
    (5, "Gym Membership", "Client E", ["Flask"], "vps", True, "", 2880, 180, 0),
    (6, "Property Listings", "Client F", ["Laravel"], "cpanel", False, "", 86400, 360, 0),
    (7, "Inventory Tracker", "Client G", ["Laravel"], "vps", True, "", 380, 200, 0),
    (8, "Salon Appointments", "Client H", ["Laravel", "Flutter"], "vps", True, "", 7200, 260, 0),
    (9, "Hotel Booking", "Client I", ["Laravel"], "vps", True, "", 8640, 220, 0),
    (10, "Transport Fleet", "Client J", ["Flask"], "vps", False, "", 129600, 330, 0),
    (11, "Event Tickets", "Client K", ["Laravel"], "cpanel", True, "Home page changed unexpectedly", 20160, 280, 0),
    (12, "Pharmacy Orders", "Client L", ["Laravel", "Flutter"], "vps", True, "", 2000, 210, 0),
]

CHANGES = [
    # project, title, status, min ago, sent (min ago) | None, decided (min ago) | None, live (min ago) | None, ask, files, tests
    (1, "Search by vehicle number on the entry screen", "client", 420, 300, None, None,
     "Client wants a search box on the entry screen. Typing a vehicle number should show that vehicle's entries.",
     ["app/Http/Controllers/EntryController.php", "resources/views/entries/index.blade.php", "routes/web.php"], (24, 24)),
    (1, "Show receipt date as DD-MM-YYYY", "passed", 90, None, None, None,
     "On the parking receipt the date shows 2026-10-14. Client wants 14-10-2026.",
     ["resources/views/receipts/print.blade.php"], (24, 24)),
    (2, "Doctor can block a full day", "beta", 25, None, None, None,
     "Doctors want to block a whole day so patients can't book it.", [], None),
    (4, "Show delivery charge before checkout", "beta", 300, None, None, None,
     "Show the delivery charge on the cart page, before the customer taps checkout.",
     ["app/Services/CartService.php", "resources/views/cart/show.blade.php"], (29, 31)),
    (4, "Add a coupon code field", "beta", 60, None, None, None, "Add a coupon code box on the cart page.", [], None),
    (4, "Change the order-ready notification text", "passed", 200, None, None, None,
     "Change the push text to: Your order is ready for pickup.", ["lang/en/notifications.php"], (31, 31)),
    (5, "Monthly plan renewal reminder", "approved", 2000, 1440, 90, None,
     "Send members a reminder 3 days before the monthly plan ends.",
     ["app/jobs/renewal_reminder.py", "app/templates/emails/renewal.html"], (18, 18)),
    (7, "Low-stock report as Excel", "client", 900, 600, None, None, "Add an Excel download on the low-stock report.",
     ["app/Exports/LowStockExport.php", "resources/views/reports/low-stock.blade.php"], (40, 40)),
    (7, "Barcode on the product label", "client", 700, 500, None, None, "Print the product barcode on the label.",
     ["resources/views/labels/product.blade.php", "composer.json"], (40, 40)),
    (9, "Room photo gallery on the booking page", "client", 6500, 5760, None, None,
     "Show 4-5 room photos on the booking page as a slider.",
     ["resources/views/booking/room.blade.php", "public/js/gallery.js"], (22, 22)),
    (12, "File size note on prescription upload", "passed", 400, None, None, None,
     "Under the prescription upload, show: Max 5 MB, photo or PDF.", ["resources/views/orders/upload.blade.php"], (27, 27)),
    (2, "Bigger appointment buttons on mobile", "live", 3000, 2800, 2000, 1500,
     "Appointment time buttons are too small on phones.", ["resources/css/booking.css"], (33, 33)),
]

ALERTS = [
    (1, "down", "Parking Manager: live site down", 12),
    (11, "page_change", "Event Tickets: home page changed", 35),
    (5, "approval", "Gym Membership: client approved a change", 90),
    (3, "backup_failed", "School ERP: last 2 backups failed", 120),
]


def commit_hash(seed):
    return hashlib.sha1(str(seed).encode()).hexdigest()[:7]


def slugify(s):
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-").replace("--", "-")


def summary_for(title, files, tests):
    if not tests:
        return "Working on this on beta now. I will run the tests when the change is done."
    passed, total = tests
    t = f"All {total} tests passed." if passed == total else f"{passed} of {total} tests passed. {total - passed} failed — I'm fixing them on beta."
    s = f"Done on beta: {title[0].lower() + title[1:]}. I changed {len(files)} file{'s' if len(files) != 1 else ''}. {t}"
    return s + (" Please check it on beta, then send it to the client." if passed == total else "")


def run_seed():
    owner = None
    users = {}
    for name, email, mobile, role, _, active, last in USERS:
        u = User(name=name, email=email, mobile=mobile, role=role, is_active=active, last_login_at=ago(last))
        u.set_password(DEMO_PASSWORD)
        db.session.add(u)
        users[email] = u
        if role == "owner":
            owner = u
    db.session.flush()

    projects = {}
    for pid, name, client, tech, host, active, incident, deploy_min, backup_min, fails in PROJECTS:
        slug = slugify(name)
        p = Project(id=pid, name=name, slug=slug, client_name=client, tech_stack=tech, host_type=host, is_active=active,
                    github_repo=f"company-org/{slug}", incident=incident, incident_at=ago(12) if incident else None,
                    monitor_enabled=False)   # demo addresses are made up — never watch them
        server = "vps-2.company.test" if host == "vps" else "cpanel.client-host.test"
        p.environments = [
            Environment(kind="beta", url=f"https://beta.{slug}.example.com", branch="beta", server_host=server,
                        deploy_path=f"/var/www/{slug}-beta", db_name=f"{slug.replace('-', '_')}_beta",
                        current_commit=commit_hash(pid * 7 + 1), last_sync_at=ago(deploy_min + 60),
                        test_numbers="+91 98765 44821"),
            Environment(kind="live", url=f"https://{slug}.example.com", branch="main", server_host=server,
                        deploy_path=f"/var/www/{slug}", db_name=slug.replace("-", "_"), current_commit=commit_hash(pid * 7)),
        ]
        db.session.add(p)
        projects[pid] = p
    db.session.flush()

    for name, email, mobile, role, pids, active, last in USERS:
        for pid in pids:
            db.session.add(ProjectMember(user_id=users[email].id, project_id=pid))

    for pid, name, client, tech, host, active, incident, deploy_min, backup_min, fails in PROJECTS:
        p = projects[pid]
        live = p.env("live")
        size_db = (40 + pid * 37 % 600) * 1024 * 1024
        size_files = (300 + pid * 113 % 2400) * 1024 * 1024
        for k, extra in enumerate((0, 4320, 10080)):
            snap = Backup(project_id=pid, kind="snapshot", size_bytes=size_db, created_at=ago(deploy_min + extra + 1),
                          storage_url=f"r2://backups/{p.slug}/snapshot-{k}.sql.gz")
            db.session.add(snap)
            db.session.flush()
            db.session.add(Deployment(project_id=pid, environment_id=live.id,
                                      commit=commit_hash(pid * 7) if k == 0 else commit_hash(pid * 7 + 20 + k),
                                      snapshot_id=snap.id, by_user_id=owner.id, created_at=ago(deploy_min + extra),
                                      note="Latest release" if k == 0 else "Earlier release"))
        for day in range(6):
            at = backup_min + day * 1440
            db.session.add(Backup(project_id=pid, kind="db", size_bytes=size_db, created_at=ago(at),
                                  storage_url=f"r2://backups/{p.slug}/db-{day}.sql.gz"))
            db.session.add(Backup(project_id=pid, kind="files", size_bytes=size_files, created_at=ago(at - 5),
                                  storage_url=f"r2://backups/{p.slug}/files-{day}.tar.gz"))
        for f in range(fails):
            db.session.add(Backup(project_id=pid, kind="db", ok=False, created_at=ago(120 + f * 1440),
                                  error="Could not reach the database server"))

    for pid, title, status, mins, sent, decided, live_min, ask, files, tests in CHANGES:
        c = Change(project_id=pid, title=title, status=status, created_at=ago(mins), requested_by_id=owner.id,
                   files=files, tests_passed=tests[0] if tests else None, tests_total=tests[1] if tests else None,
                   live_at=ago(live_min) if live_min is not None else None)
        db.session.add(c)
        db.session.flush()
        c.branch = f"change/{c.id}-{slugify(title)[:40]}"
        c.ai_summary = summary_for(title, files, tests)
        db.session.add(ChangeMessage(change_id=c.id, sender="owner", user_id=owner.id, body=ask, created_at=ago(mins)))
        db.session.add(ChangeMessage(change_id=c.id, sender="claude", body=c.ai_summary, created_at=ago(mins - 6)))
        if sent is not None:
            db.session.add(Approval(change_id=c.id, token=sha256(secrets.token_urlsafe(24)), sent_at=ago(sent),
                                    expires_at=ago(sent) + timedelta(days=7),
                                    checklist=["Open the test site", f"Try: {title}", "Check that nothing else looks different"],
                                    decision="approved" if decided is not None else None,
                                    decided_at=ago(decided) if decided is not None else None))

    for pid, kind, msg, mins in ALERTS:
        db.session.add(Alert(project_id=pid, kind=kind, message=msg, created_at=ago(mins)))

    dev1 = users["dev@demo.test"]
    for uid, pid, action, details, prompt, mins in [
        (None, 1, "Alert", "Monitor: live site not responding", "", 12),
        (None, 5, "Client approved", "Monthly plan renewal reminder", "", 90),
        (owner.id, 1, "Prompt", "New change: Show receipt date as DD-MM-YYYY",
         "On the parking receipt the date shows 2026-10-14. Client wants 14-10-2026.", 90),
        (owner.id, 1, "Go Live", "Deployed to live after snapshot", "", 140),
        (dev1.id, 7, "Sign in", "Password + OTP", "", 180),
        (owner.id, 1, "Sent to client", "Search by vehicle number on the entry screen", "", 300),
        (owner.id, 4, "Prompt", "New change: Show delivery charge before checkout",
         "Show the delivery charge on the cart page, before the customer taps checkout.", 300),
        (owner.id, 2, "Go Live", "Bigger appointment buttons on mobile", "", 1500),
        (owner.id, 3, "Access off", "Developer 3 — access turned off", "", 20160),
    ]:
        db.session.add(ActivityLog(user_id=uid, project_id=pid, action=action, details=details, prompt_text=prompt,
                                   created_at=ago(mins)))
    db.session.commit()


def register(app):
    @app.cli.command("seed")
    def seed_command():
        """Fill an empty database with the demo data (same as the mock)."""
        if User.query.first():
            raise click.ClickException("The database already has users — seed only runs on an empty database.")
        run_seed()
        click.echo(f"Seeded: {User.query.count()} users, {Project.query.count()} projects, {Change.query.count()} changes, "
                   f"{Backup.query.count()} backups, {Deployment.query.count()} deployments. Demo password: {DEMO_PASSWORD}")
