from lemco_portal.install import create_rma_print_formats


def execute():
	"""Site is already installed (after_install already ran once), so this
	patch is what actually creates/updates the four RMA print formats on
	upgrade. Safe to re-run - overwrites the HTML of existing ones by name
	rather than duplicating."""
	create_rma_print_formats()
