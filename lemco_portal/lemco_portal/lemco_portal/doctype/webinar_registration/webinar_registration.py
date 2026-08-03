import frappe
from frappe.model.document import Document


class WebinarRegistration(Document):
	def validate(self):
		exists = frappe.db.exists(
			"Webinar Registration",
			{
				"webinar": self.webinar,
				"customer": self.customer,
				"name": ["!=", self.name],
			},
		)
		if exists:
			frappe.throw("This customer is already registered for this webinar.")
