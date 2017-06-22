from django import template
from threadlocals.threadlocals import get_current_request

from ecommerce.enterprise import utils
from ecommerce.enterprise.exceptions import EnterpriseDoesNotExist

register = template.Library()


@register.assignment_tag()
def enterprise_customer_for_voucher(voucher):
    """
    Retrieve enterprise customer associated with the given voucher.

    Raises:
        EnterpriseDoesNotExist: Voucher is not associated with any enterprise customer.
    """
    request = get_current_request()
    try:
        return utils.get_enterprise_customer_from_voucher(request and request.site, voucher)
    except EnterpriseDoesNotExist:
        return None
