from lemco_portal.install import add_item_rma_field


def execute():
	"""Site is already installed (after_install already ran once), so this
	patch is what actually adds Item.allow_rma on upgrade. Safe to re-run."""
	add_item_rma_field()
