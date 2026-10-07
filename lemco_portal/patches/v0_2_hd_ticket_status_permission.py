from lemco_portal.install import add_hd_ticket_status_read_permission


def execute():
	"""Site is already installed (after_install already ran once), so this
	patch is what actually grants Customer/HD Customer/HD Customer Manager
	read access to HD Ticket Status on upgrade. Safe to re-run."""
	add_hd_ticket_status_read_permission()
