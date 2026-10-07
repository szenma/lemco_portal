import frappe
from frappe import _
from frappe.model.document import Document


class RMAUnit(Document):
	def autoname(self):
		"""Names each unit <RMA>-<counter>, e.g. RMA-2026-00142-01,
		RMA-2026-00142-02 - see spec section 34. Declarative autoname
		patterns can't express "counter scoped to the parent RMA", so this
		is a small script-based autoname instead."""
		if not self.rma:
			frappe.throw(_("RMA is required before an RMA Unit can be created."))

		existing = frappe.db.count("RMA Unit", {"rma": self.rma})
		self.name = f"{self.rma}-{existing + 1:02d}"
