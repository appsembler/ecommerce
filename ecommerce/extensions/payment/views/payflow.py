""" Payflow payment processing views """
import logging

from django.views.generic import View
from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ObjectDoesNotExist
from django.core.urlresolvers import reverse
from django.shortcuts import redirect
from django.http import HttpResponse

from oscar.apps.partner import strategy
from oscar.core.loading import get_class, get_model
from oscar.apps.payment.exceptions import PaymentError, TransactionDeclined

from ecommerce.extensions.checkout.mixins import EdxOrderPlacementMixin
from ecommerce.extensions.checkout.utils import get_receipt_page_url
from ecommerce.extensions.payment.exceptions import InvalidSignatureError
from ecommerce.extensions.payment.processors.payflow import Payflow

logger = logging.getLogger(__name__)

Applicator = get_class('offer.utils', 'Applicator')
Basket = get_model('basket', 'Basket')
BillingAddress = get_model('order', 'BillingAddress')
Country = get_model('address', 'Country')
NoShippingRequired = get_class('shipping.methods', 'NoShippingRequired')
OrderNumberGenerator = get_class('order.utils', 'OrderNumberGenerator')
OrderTotalCalculator = get_class('checkout.calculators', 'OrderTotalCalculator')


class PayflowPaymentResponseView(EdxOrderPlacementMixin, View):
    """ Validates a response from Payflow and processes the associated basket/order appropriately. """

    @property
    def payment_processor(self):
        return Payflow(self.request.site)

    # Disable atomicity for the view. Otherwise, we'd be unable to commit to the database
    # until the request had concluded; Django will refuse to commit when an atomic() block
    # is active, since that would break atomicity. Without an order present in the database
    # at the time fulfillment is attempted, asynchronous order fulfillment tasks will fail.
    @method_decorator(transaction.non_atomic_requests)
    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super(PayflowPaymentResponseView, self).dispatch(request, *args, **kwargs)

    def _get_basket(self, basket_id):
        if not basket_id:
            return None

        try:
            basket_id = int(basket_id)
            basket = Basket.objects.get(id=basket_id)
            basket.strategy = strategy.Default()
            Applicator().apply(basket, basket.owner, self.request)
            return basket
        except (ValueError, ObjectDoesNotExist):
            return None

    def post(self, request):
        """Process a Payflow merchant notification and place an order for paid products as appropriate."""

        # Note (CCB): Orders should not be created until the payment processor has validated the response's signature.
        # This validation is performed in the handle_payment method. After that method succeeds, the response can be
        # safely assumed to have originated from Payflow.

        payflow_response = request.POST.dict()
        basket = None
        transaction_id = None

        try:
            transaction_id = payflow_response.get('PNREF')
            order_number = payflow_response.get('PONUM')
            basket_id = OrderNumberGenerator().basket_id(order_number)

            logger.info(
                'Received Payflow merchant notification for transaction [%s], associated with basket [%d].',
                transaction_id,
                basket_id
            )

            basket = self._get_basket(basket_id)

            if not basket:
                logger.error('Received payment for non-existent basket [%s].', basket_id)
                return HttpResponse(status=400)
        finally:
            # Store the response in the database.
            ppr = self.payment_processor.record_processor_response(payflow_response, transaction_id=transaction_id,
                                                                   basket=basket)

        try:
            # Explicitly delimit operations which will be rolled back if an exception occurs.
            with transaction.atomic():
                try:
                    self.handle_payment(payflow_response, basket)
                except InvalidSignatureError:
                    logger.exception(
                        'Received an invalid Payflow response. The payment response was recorded in entry [%d].',
                        ppr.id
                    )
                    return HttpResponse(status=400)
                except TransactionDeclined as exception:
                    logger.info(
                        'Payflow payment did not complete for basket [%d] because [%s]. '
                        'The payment response was recorded in entry [%d].',
                        basket.id,
                        exception.__class__.__name__,
                        ppr.id
                    )
                    return HttpResponse()
                except PaymentError:
                    logger.exception(
                        'Payflow payment failed for basket [%d]. The payment response was recorded in entry [%d].',
                        basket.id,
                        ppr.id
                    )
                    return HttpResponse()
        except:  # pylint: disable=bare-except
            logger.exception('Attempts to handle payment for basket [%d] failed.', basket.id)
            return HttpResponse(status=500)

        try:
            # Note (CCB): In the future, if we do end up shipping physical products, we will need to
            # properly implement shipping methods. For more, see
            # http://django-oscar.readthedocs.org/en/latest/howto/how_to_configure_shipping.html.
            shipping_method = NoShippingRequired()
            shipping_charge = shipping_method.calculate(basket)

            # Note (CCB): This calculation assumes the payment processor has not sent a partial authorization,
            # thus we use the amounts stored in the database rather than those received from the payment processor.
            user = basket.owner
            order_total = OrderTotalCalculator().calculate(basket, shipping_charge)
            billing_address = payflow_response.get('SHIPTOCOUNTRY')

            self.handle_order_placement(
                order_number,
                user,
                basket,
                None,
                shipping_method,
                shipping_charge,
                billing_address,
                order_total,
                request=request,
            )

            return HttpResponse()
        except:  # pylint: disable=bare-except
            logger.exception(self.order_placement_failure_msg, basket.id)
            return HttpResponse(status=500)

    def get(self, request, *args, **kwargs):
        """Handle an incoming user returned to us by Payflow after processing payment."""

        payflow_response = request.GET.dict()
        try:
            transaction_id = payflow_response.get('PNREF')
            order_number = payflow_response.get('PONUM')
            basket_id = OrderNumberGenerator().basket_id(order_number)

            logger.info(
                'Received Payflow payer notification for transaction [%s], associated with basket [%d].',
                transaction_id,
                basket_id
            )

            basket = self._get_basket(basket_id)

            if not basket:
                logger.error('Received payer notification for non-existent basket [%s].', basket_id)
                return redirect(reverse('payment_error'))
        except:  # pylint: disable=bare-except
            return redirect(reverse('payment_error'))

        receipt_url = get_receipt_page_url(
            order_number=basket.order_number,
            site_configuration=basket.site.siteconfiguration
        )

        try:
            with transaction.atomic():
                try:
                    CARDTYPE = payflow_response['CARDTYPE']
                    CARDTYPES = {"0":"Visa", "1":"MasterCard", "2":"Discover", "3":"American Express", "4":"Diner's Club", "5":"JCB"}
                    payflow_response['CARDTYPE'] = CARDTYPES[CARDTYPE]
                    self.handle_payment(payflow_response, basket)
                    self.payment_processor.record_processor_response(payflow_response, transaction_id=transaction_id, basket=basket)
                except PaymentError:
                    return redirect(self.payment_processor.error_url)
        except:  # pylint: disable=bare-except
            logger.exception('Attempts to handle payment for basket [%d] failed.', basket.id)
            return redirect(receipt_url)
        try:
            shipping_method = NoShippingRequired()
            shipping_charge = shipping_method.calculate(basket)
            order_total = OrderTotalCalculator().calculate(basket, shipping_charge)

            user = basket.owner
            # Given a basket, order number generation is idempotent. Although we've already
            # generated this order number once before, it's faster to generate it again
            # than to retrieve an invoice number from PayPal.
            order_number = basket.order_number

            self.handle_order_placement(
                order_number=order_number,
                user=user,
                basket=basket,
                shipping_address=None,
                shipping_method=shipping_method,
                shipping_charge=shipping_charge,
                billing_address=None,
                order_total=order_total,
                request=request
            )

            return redirect(receipt_url)
        except:  # pylint: disable=bare-except
            logger.exception(self.order_placement_failure_msg, basket.id)
            return redirect(receipt_url)
