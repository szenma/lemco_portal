frappe.pages['lemco-portal'].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Lemco Portal',
		single_column: true,
	});

	// Hide the default page title bar so the hero banner is the first thing seen.
	// Comment this out if you want to keep Frappe's standard page header.
	$(page.wrapper).find('.page-head').hide();

	new LemcoPortal(page);
};

class LemcoPortal {
	constructor(page) {
		this.page = page;
		this.load_stats().then((stats) => this.render(stats));
	}

	// Pulls counts from the Helpdesk, LMS, and CRM apps.
	// Verify the exact field/status names against your own site (they can
	// differ if any of these apps have been customised) - the filters below
	// are the common defaults for each app.
	async load_stats() {
		const safe_count = async (doctype, filters) => {
			try {
				return await frappe.db.count(doctype, { filters });
			} catch (e) {
				// doctype not installed, or user has no read access to it
				return 0;
			}
		};

		const [
			// Helpdesk: HD Ticket.status is one of
			// Open / Replied / Resolved / Closed (may vary if customised)
			tickets_open, tickets_closed,

			// LMS: LMS Enrollment.progress is 0-100
			courses_in_progress, courses_completed,

			// CRM: CRM Lead - "converted" is a checkbox (0/1) set once a
			// lead turns into a deal
			leads_open, leads_converted,

			// CRM: CRM Deal.status stores the *name* of a CRM Deal Status
			// record. Default installs ship "Won" / "Lost" as the closing
			// statuses - adjust this list if you've renamed/added statuses.
			deals_open, deals_closed,
		] = await Promise.all([
			safe_count('HD Ticket', { status: ['not in', ['Closed', 'Resolved']] }),
			safe_count('HD Ticket', { status: ['in', ['Closed', 'Resolved']] }),

			safe_count('LMS Enrollment', { progress: ['<', 100] }),
			safe_count('LMS Enrollment', { progress: ['>=', 100] }),

			safe_count('CRM Lead', { converted: 0 }),
			safe_count('CRM Lead', { converted: 1 }),

			safe_count('CRM Deal', { status: ['not in', ['Won', 'Lost']] }),
			safe_count('CRM Deal', { status: ['in', ['Won', 'Lost']] }),
		]);

		return {
			tickets: { open: tickets_open, closed: tickets_closed },
			courses: { open: courses_in_progress, closed: courses_completed },
			leads: { open: leads_open, closed: leads_converted },
			deals: { open: deals_open, closed: deals_closed },
		};
	}

	render(stats) {
		const t = stats.tickets, c = stats.courses, l = stats.leads, d = stats.deals;

		$(this.page.body).html(`
			<div class="lemco-portal">
				<div class="lp-hero">
					<h1>Welcome to Lemco Portal!</h1>
					<p>Here's what's happening with your account today.</p>
				</div>

				<div class="lp-cards">
					${this.stat_card('orange', 'ticket', 'My Tickets', t.open + t.closed, 'Total tickets', 'Open', t.open, 'Closed', t.closed, '/helpdesk/tickets', 'View and manage')}
					${this.stat_card('blue', 'education', 'My Courses', c.open + c.closed, 'Enrolled courses', 'In progress', c.open, 'Completed', c.closed, '/lms/courses', 'Continue learning')}
					${this.stat_card('purple', 'small-file', 'My Leads', l.open + l.closed, 'Total leads', 'Open', l.open, 'Converted', l.closed, '/crm/leads', 'View leads')}
					${this.stat_card('red', 'tool', 'My Deals', d.open + d.closed, 'Total deals', 'Open', d.open, 'Closed', d.closed, '/crm/deals', 'View deals')}
				</div>

				<h2 class="lp-section-title">Quick access</h2>
				<div class="lp-quick-access">
					${this.qa_card('blue', 'book', 'Knowledge base', 'Browse Helpdesk articles and find helpful resources', '/helpdesk/kb')}
					${this.qa_card('purple', 'education', 'Academy', 'Access LMS courses and learning paths', '/lms')}
					${this.qa_card('green', 'users', 'Users', 'Manage users and permissions', '/app/user')}
				</div>

				<div class="lp-bottom-row">
					<div class="lp-panel">
						<div class="lp-panel-head">
							<h2>Upcoming Webinars</h2>
							<a class="lp-see-all" href="/webinars">View all webinars →</a>
						</div>
						<div id="lp-webinar-list"></div>
					</div>

					<div class="lp-panel">
						<h2>System status</h2>
						<div class="lp-status-ok"><span class="lp-dot"></span>All systems operational</div>
						<p class="lp-status-updated">Last checked 2 minutes ago</p>
						<div class="lp-status-row"><span>Portal</span><span>Operational</span></div>
						<div class="lp-status-row"><span>Services</span><span>Operational</span></div>
						<div class="lp-status-row"><span>Integrations</span><span>Operational</span></div>
						<button class="lp-status-btn">View status page</button>
					</div>
				</div>
			</div>
		`);

		this.render_webinars();
	}

	stat_card(color, icon, label, count, subtext, pill1_label, pill1_value, pill2_label, pill2_value, link, link_text) {
		return `
			<div class="lp-card">
				<div class="lp-card-head">
					<div class="lp-icon-box lp-${color}">${frappe.utils.icon(icon, 'md')}</div>
					<div class="lp-label">${label}</div>
				</div>
				<div class="lp-count">${count}</div>
				<div class="lp-subtext">${subtext}</div>
				<div class="lp-pills">
					<span class="lp-pill lp-pill-${color}">${pill1_label}: ${pill1_value}</span>
					<span class="lp-pill lp-pill-grey">${pill2_label}: ${pill2_value}</span>
				</div>
				<a class="lp-card-link lp-text-${color}" href="${link}">${link_text} →</a>
			</div>`;
	}

	qa_card(color, icon, title, desc, link) {
		return `
			<a class="lp-qa-card lp-qa-${color}" href="${link}">
				<div class="lp-qa-icon lp-${color}">${frappe.utils.icon(icon, 'md')}</div>
				<div><strong>${title}</strong><span>${desc}</span></div>
				<div class="lp-qa-arrow">→</div>
			</a>`;
	}

	render_webinars() {
		// Swap this for a frappe.call() to your own "Webinar" doctype.
		const webinars = [
			{ mon: 'MAY', day: '28', dow: 'WED', title: 'Fleex Prime Overview', desc: 'Explore the core capabilities and latest innovations in Fleex Prime.', duration: '45 min' },
			{ mon: 'JUN', day: '04', dow: 'WED', title: 'Customer Portal Training', desc: "Learn how to get the most out of the Lemco Customer Portal.", duration: '60 min' },
			{ mon: 'JUN', day: '11', dow: 'WED', title: 'Pro Line Headend Technical Session', desc: 'Deep dive into Pro Line headend solutions, performance and configuration.', duration: '75 min' },
		];

		const html = webinars.map(w => `
			<div class="lp-webinar">
				<div class="lp-date-box"><div class="lp-mon">${w.mon}</div><div class="lp-day">${w.day}</div><div class="lp-dow">${w.dow}</div></div>
				<div class="lp-webinar-info">
					<strong>${w.title}</strong>
					<p>${w.desc}</p>
					<div class="lp-meta">Hosted on Microsoft Teams</div>
				</div>
				<div class="lp-duration">${w.duration}</div>
				<button class="lp-register-btn">Register</button>
			</div>
		`).join('');

		$('#lp-webinar-list').html(html);
	}
}
