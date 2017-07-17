"""
Decorators related to enterprise functionality.
"""
from functools import wraps

from oscar.core.loading import get_model

from ecommerce.enterprise import utils

Voucher = get_model('voucher', 'Voucher')


def set_enterprise_cookie(func, max_age=None):
    """
    Decorator for applying cookie with enterprise customer uuid.

    Arguments:
        func (function): The function to decorate.
        max_age (int): The max_age to set on the enterprise cookie (seconds).

    Returns:
        function: The decorated function.
    """
    @wraps(func)
    def _decorated(request, *args, **kwargs):
        response = func(request, *args, **kwargs)

        # Set enterprise customer cookie if enterprise customer uuid is available.
        code, enterprise_customer_uuid = request.GET.get('code'), None
        if code:
            enterprise_customer_uuid = utils.get_enterprise_customer_uuid(code)
            if enterprise_customer_uuid:
                response = utils.set_enterprise_customer_cookie(
                    request.site,
                    response,
                    enterprise_customer_uuid,
                    max_age=max_age
                )

        return response
    return _decorated
