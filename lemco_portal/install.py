import frappe


def after_install():
	add_portal_access_field()


def add_portal_access_field():
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Customer": [
				{
					"fieldname": "portal_access",
					"label": "Portal Access",
					"fieldtype": "Select",
					"options": "No\nYes",
					"default": "No",
					"insert_after": "customer_name",
					"in_standard_filter": 1,
					"description": (
						"Controls whether this customer's users can log into "
						"the customer portal. Set to Yes to activate access "
						"after verifying the customer."
					),
				}
			]
		}
	)
