from lemco_portal.install import create_repair_service_workspace


def execute():
	"""Site is already installed (after_install already ran once), so this
	patch is what actually creates the Repair Service workspace + its
	Number Cards on upgrade. Safe to re-run."""
	create_repair_service_workspace()
