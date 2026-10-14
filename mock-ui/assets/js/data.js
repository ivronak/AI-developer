/* Mock seed data — generic sample names only. Times are "minutes ago"; app.js turns them into timestamps
   when it builds the demo state, so the mock always looks fresh. Bump DATA_VERSION when the shape changes. */
window.DEMO = {
  DATA_VERSION: 2,
  PASSWORD: "demo1234",
  OTP: "123456",
  MAX_TRIES: 5,
  LOCK_MIN: 15,
  BACKUP_STALE_MIN: 26 * 60,
  APPROVAL_SLOW_MIN: 3 * 1440,
  LINK_DAYS: 7,

  users: [
    { id: 1, name: "Owner", email: "owner@demo.test", mobile: "+91 98765 44821", role: "owner", projects: "all", active: true, lastMin: 2 },
    { id: 2, name: "Developer 1", email: "dev@demo.test", mobile: "+91 98765 11203", role: "developer", projects: [2, 5, 7], active: true, lastMin: 180 },
    { id: 3, name: "Developer 2", email: "dev2@demo.test", mobile: "+91 98765 33310", role: "developer", projects: [4, 12], active: true, lastMin: 1500 },
    { id: 4, name: "Developer 3", email: "dev3@demo.test", mobile: "+91 98765 70045", role: "developer", projects: [3], active: false, lastMin: 20160 }
  ],

  /* incident = a live problem the monitor found (F15). Health is computed from it plus backups, tests and approvals. */
  projects: [
    { id: 1, name: "Parking Manager", client: "Client A", tech: ["Laravel", "Flutter"], host: "VPS", active: true, incident: "Live site down for 12 min", deployMin: 140, backupMin: 300 },
    { id: 2, name: "Clinic Booking", client: "Client B", tech: ["Laravel"], host: "VPS", active: true, incident: "", deployMin: 1500, backupMin: 240 },
    { id: 3, name: "School ERP", client: "Client C", tech: ["Laravel", "Flutter"], host: "VPS", active: true, incident: "", deployMin: 4320, backupMin: 3000, backupFails: 2 },
    { id: 4, name: "Food Delivery", client: "Client D", tech: ["Laravel", "Flutter"], host: "cPanel", active: true, incident: "", deployMin: 600, backupMin: 420 },
    { id: 5, name: "Gym Membership", client: "Client E", tech: ["Flask"], host: "VPS", active: true, incident: "", deployMin: 2880, backupMin: 180 },
    { id: 6, name: "Property Listings", client: "Client F", tech: ["Laravel"], host: "cPanel", active: false, incident: "", deployMin: 86400, backupMin: 360 },
    { id: 7, name: "Inventory Tracker", client: "Client G", tech: ["Laravel"], host: "VPS", active: true, incident: "", deployMin: 380, backupMin: 200 },
    { id: 8, name: "Salon Appointments", client: "Client H", tech: ["Laravel", "Flutter"], host: "VPS", active: true, incident: "", deployMin: 7200, backupMin: 260 },
    { id: 9, name: "Hotel Booking", client: "Client I", tech: ["Laravel"], host: "VPS", active: true, incident: "", deployMin: 8640, backupMin: 220 },
    { id: 10, name: "Transport Fleet", client: "Client J", tech: ["Flask"], host: "VPS", active: false, incident: "", deployMin: 129600, backupMin: 330 },
    { id: 11, name: "Event Tickets", client: "Client K", tech: ["Laravel"], host: "cPanel", active: true, incident: "Home page changed unexpectedly", deployMin: 20160, backupMin: 280 },
    { id: 12, name: "Pharmacy Orders", client: "Client L", tech: ["Laravel", "Flutter"], host: "VPS", active: true, incident: "", deployMin: 2000, backupMin: 210 }
  ],

  /* status: beta (Claude working / tests failed) · passed (tests passed) · client (link sent) · changes (client asked for changes)
     · approved · live */
  changes: [
    { id: 1, projectId: 1, title: "Search by vehicle number on the entry screen", status: "client", min: 420, sentMin: 300,
      ask: "Client wants a search box on the entry screen. Typing a vehicle number should show that vehicle's entries.",
      files: ["app/Http/Controllers/EntryController.php", "resources/views/entries/index.blade.php", "routes/web.php"],
      tests: [24, 24], shot: "entry-screen.png" },
    { id: 2, projectId: 1, title: "Show receipt date as DD-MM-YYYY", status: "passed", min: 90,
      ask: "On the parking receipt the date shows 2026-10-14. Client wants 14-10-2026.",
      files: ["resources/views/receipts/print.blade.php"], tests: [24, 24] },
    { id: 3, projectId: 2, title: "Doctor can block a full day", status: "beta", min: 25,
      ask: "Doctors want to block a whole day so patients can't book it.", files: [], tests: null },
    { id: 4, projectId: 4, title: "Show delivery charge before checkout", status: "beta", min: 300,
      ask: "Show the delivery charge on the cart page, before the customer taps checkout.",
      files: ["app/Services/CartService.php", "resources/views/cart/show.blade.php"], tests: [29, 31] },
    { id: 5, projectId: 4, title: "Add a coupon code field", status: "beta", min: 60,
      ask: "Add a coupon code box on the cart page.", files: [], tests: null },
    { id: 6, projectId: 4, title: "Change the order-ready notification text", status: "passed", min: 200,
      ask: "Change the push text to: Your order is ready for pickup.", files: ["lang/en/notifications.php"], tests: [31, 31] },
    { id: 7, projectId: 5, title: "Monthly plan renewal reminder", status: "approved", min: 2000, sentMin: 1440, decidedMin: 90,
      ask: "Send members a reminder 3 days before the monthly plan ends.",
      files: ["app/jobs/renewal_reminder.py", "app/templates/emails/renewal.html"], tests: [18, 18] },
    { id: 8, projectId: 7, title: "Low-stock report as Excel", status: "client", min: 900, sentMin: 600,
      ask: "Add an Excel download on the low-stock report.", files: ["app/Exports/LowStockExport.php", "resources/views/reports/low-stock.blade.php"], tests: [40, 40] },
    { id: 9, projectId: 7, title: "Barcode on the product label", status: "client", min: 700, sentMin: 500,
      ask: "Print the product barcode on the label.", files: ["resources/views/labels/product.blade.php", "composer.json"], tests: [40, 40] },
    { id: 10, projectId: 9, title: "Room photo gallery on the booking page", status: "client", min: 6500, sentMin: 5760,
      ask: "Show 4-5 room photos on the booking page as a slider.", files: ["resources/views/booking/room.blade.php", "public/js/gallery.js"], tests: [22, 22] },
    { id: 11, projectId: 12, title: "File size note on prescription upload", status: "passed", min: 400,
      ask: "Under the prescription upload, show: Max 5 MB, photo or PDF.", files: ["resources/views/orders/upload.blade.php"], tests: [27, 27] },
    { id: 12, projectId: 2, title: "Bigger appointment buttons on mobile", status: "live", min: 3000, sentMin: 2800, decidedMin: 2000, liveMin: 1500,
      ask: "Appointment time buttons are too small on phones.", files: ["resources/css/booking.css"], tests: [33, 33] }
  ],

  alerts: [
    { projectId: 1, text: "Parking Manager: live site down", min: 12 },
    { projectId: 11, text: "Event Tickets: home page changed", min: 35 },
    { projectId: 5, text: "Gym Membership: client approved a change", min: 90 },
    { projectId: 3, text: "School ERP: last 2 backups failed", min: 120 }
  ],

  log: [
    { min: 12, userId: 0, projectId: 1, action: "Alert", details: "Monitor: live site not responding" },
    { min: 90, userId: 0, projectId: 5, action: "Client approved", details: "Monthly plan renewal reminder" },
    { min: 90, userId: 1, projectId: 1, action: "Prompt", details: "New change: Show receipt date as DD-MM-YYYY", prompt: "On the parking receipt the date shows 2026-10-14. Client wants 14-10-2026." },
    { min: 140, userId: 1, projectId: 1, action: "Go Live", details: "Deployed to live after snapshot" },
    { min: 180, userId: 2, projectId: 7, action: "Sign in", details: "Password + OTP" },
    { min: 300, userId: 1, projectId: 1, action: "Sent to client", details: "Search by vehicle number on the entry screen" },
    { min: 300, userId: 1, projectId: 4, action: "Prompt", details: "New change: Show delivery charge before checkout", prompt: "Show the delivery charge on the cart page, before the customer taps checkout." },
    { min: 1500, userId: 1, projectId: 2, action: "Go Live", details: "Bigger appointment buttons on mobile" },
    { min: 20160, userId: 1, projectId: 3, action: "Access off", details: "Developer 3 — access turned off" }
  ]
};
