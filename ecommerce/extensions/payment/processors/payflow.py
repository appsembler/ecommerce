""" Payflow payment processing. """
from __future__ import unicode_literals

import logging
from decimal import Decimal
from urlparse import urljoin
from urllib import urlencode
import urlparse
from httplib2 import Http
import random
import string
import yaml

import waffle
from django.core.urlresolvers import reverse
from django.utils.functional import cached_property
from oscar.apps.payment.exceptions import GatewayError

from ecommerce.core.url_utils import get_ecommerce_url
from ecommerce.extensions.payment.models import PaypalWebProfile, PaypalProcessorConfiguration
from ecommerce.extensions.payment.processors import BasePaymentProcessor, HandledProcessorResponse
from ecommerce.extensions.payment.utils import middle_truncate

logger = logging.getLogger(__name__)


class Payflow(BasePaymentProcessor):
    """
    Payflow processor (November 2018)

    For reference, see https://developer.paypal.com/docs/classic/payflow/integration-guide
    """

    NAME = 'payflow'
    DEFAULT_PROFILE_NAME = 'default'

    def __init__(self, site):
        """
        Constructs a new instance of the Payflow processor.

        Raises:
            KeyError: If a required setting is not configured for this payment processor
        """
        super(Payflow, self).__init__(site)

    def token_id_generator(self):
        token_id = ''.join([random.choice(string.ascii_letters + string.digits) for n in xrange(32)])
        return token_id

    def get_transaction_parameters(self, basket, request=None, use_client_side_checkout=False, **kwargs):
        """
        Create a new Payflow payment.

        Arguments:
            basket (Basket): The basket of products being purchased.
            request (Request, optional): A Request object which is used to construct Payflow's `return_url`.
            use_client_side_checkout (bool, optional): This value is not used.
            **kwargs: Additional parameters; not used by this method.

        Returns:
            dict: Payflow-specific parameters required to complete a transaction. Must contain a URL
                to which users can be directed in order to approve a newly created payment.

        Raises:
            GatewayError: Indicates a general error or unexpected behavior on the part of Payflow which prevented
                a payment from being created.
        """

        # STEP 1 - Initializing communication to Payflow before redirecting the user to a hosted page
        h = Http()
        ECOM_ENV_TOKENS = yaml.load(open('/edx/etc/ecommerce.yml'))
        Payflow_PASSWORD = ECOM_ENV_TOKENS['PAYMENT_PROCESSOR_CONFIG']['edx']['payflow']['Payflow_PASSWORD']
        VENDOR_ID = ECOM_ENV_TOKENS['PAYMENT_PROCESSOR_CONFIG']['edx']['payflow']['VENDOR_ID']
        Payflow_USER = ECOM_ENV_TOKENS['PAYMENT_PROCESSOR_CONFIG']['edx']['payflow']['Payflow_USER']
        PAYFLOW_PARTNER = ECOM_ENV_TOKENS['PAYMENT_PROCESSOR_CONFIG']['edx']['payflow']['PAYFLOW_PARTNER']
        TRANSACTION_TYPE = ECOM_ENV_TOKENS['PAYMENT_PROCESSOR_CONFIG']['edx']['payflow']['TRANSACTION_TYPE']
        TEMPLATE_TYPE = ECOM_ENV_TOKENS['PAYMENT_PROCESSOR_CONFIG']['edx']['payflow']['TEMPLATE_TYPE']
        CURRENCY = ECOM_ENV_TOKENS['PAYMENT_PROCESSOR_CONFIG']['edx']['payflow']['CURRENCY']
        PAYFLOW_ENDPOINT = ECOM_ENV_TOKENS['PAYMENT_PROCESSOR_CONFIG']['edx']['payflow']['PAYFLOW_ENDPOINT']
        PAYFLOW_TOKEN_ENDPOINT = ECOM_ENV_TOKENS['PAYMENT_PROCESSOR_CONFIG']['edx']['payflow']['PAYFLOW_TOKEN_ENDPOINT']
        RETURNURL = ECOM_ENV_TOKENS['PAYMENT_PROCESSOR_CONFIG']['edx']['payflow']['RETURNURL']
        data = "PARTNER={}&PWD={}&VENDOR={}&USER={}&TRXTYPE={}&AMT={}&CURRENCY={}&CREATESECURETOKEN=Y&SECURETOKENID={}&RETURNURL={}".format(
            PAYFLOW_PARTNER,
            Payflow_PASSWORD,
            VENDOR_ID,
            Payflow_USER,
            TRANSACTION_TYPE,
            unicode(basket.total_incl_tax),
            CURRENCY,
            self.token_id_generator(),
            RETURNURL
            )
        resp, content = h.request(PAYFLOW_TOKEN_ENDPOINT, "POST", data)
        # 1.1 Check the Payflow response
        params_dict = urlparse.parse_qsl(content)
        params = dict(params_dict)
        # 1.2 Response is successfull and we got token
        if params["RESULT"] == '0' and params['RESPMSG'] == "Approved" :
            token = params["SECURETOKEN"]
            token_id = params["SECURETOKENID"]
        # 1.3 Payflow response wasn't successfull
        elif params["RESULT"] != 0 :
            print("Unsuccessfull token generation")
        payment_page_url = "{}?SECURETOKENID={}&SECURETOKEN={}&PONUM={}&INVNUM={}&COMMENT2={}&TEMPLATE={}&RETURNURL={}".format(
            PAYFLOW_ENDPOINT,
            token_id,
            token,
            basket.order_number,
            basket.order_number,
            basket.order_number,
            TEMPLATE_TYPE,
            RETURNURL
            )

        parameters = {
            'payment_page_url': payment_page_url,
        }

        return parameters

    @staticmethod
    def get_single_seat(basket):
        """
        Return the first product encountered in the basket with the product
        class of 'seat'.  Return None if no such products were found.
        """
        try:
            seat_class = ProductClass.objects.get(slug='seat')
        except ProductClass.DoesNotExist:
            # this occurs in test configurations where the seat product class is not in use
            return None

        for line in basket.lines.all():
            product = line.product
            if product.get_product_class() == seat_class:
                return product

        return None

    def handle_processor_response(self, response, basket=None):
        """
        Execute an approved Payflow payment.

        This method creates PaymentEvents and Sources for approved payments.

        Arguments:
            response (dict): Dictionary of parameters returned by Payflow in the `return_url` query string.

        Keyword Arguments:
            basket (Basket): Basket being purchased via the payment processor.

        Raises:
            GatewayError: Indicates a general error or unexpected behavior on the part of Payflow which prevented
                an approved payment from being executed.

        Returns:
            HandledProcessorResponse
        """

        # Raise an exception for payments that were not accepted. Consuming code should be responsible for handling
        # and logging the exception.
        transaction_state = response['RESPMSG']
        if transaction_state != "Approved" :
            raise exception

        currency = response.get('CURRENCY', '')
        total = Decimal(response.get('AMT'))
        transaction_id = response.get('PONUM')
        card_number = response.get('ACCT', '')
        card_type = response.get('CARDTYPE', '')

        return HandledProcessorResponse(
            transaction_id=transaction_id,
            total=total,
            currency=currency,
            card_number=card_number,
            card_type=card_type,
        )

    def _get_error(self, payment):
        """
        Shameful workaround for mocking the `error` attribute on instances of
        `Payflow.Payment`. The `error` attribute is created at runtime,
        but passing `create=True` to `patch()` isn't enough to mock the
        attribute in this module.
        """
        return payment.error  # pragma: no cover

    def _get_payment_sale(self, payment):
        """
        Returns the Sale related to a given Payment.

        Note (CCB): We mostly expect to have a single sale and transaction per payment. If we
        ever move to a split payment scenario, this will need to be updated.
        """
        for transaction in payment.transactions:
            for related_resource in transaction.related_resources:
                try:
                    return related_resource.sale
                except Exception:  # pylint: disable=broad-except
                    continue

        return None

    def issue_credit(self, order, reference_number, amount, currency):  # pylint: disable=unused-argument
        """
        This method should be implemented in the future in order
        to accept payment refunds
        see https://developer.paypal.com/docs/classic/payflow/integration-guide
        """

        logger.exception(
            'Payflow processor can not issue credits or refunds',
        )

        raise NotImplementedError
