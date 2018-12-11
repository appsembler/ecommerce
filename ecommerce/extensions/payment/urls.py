""" Payment-related URLs """
from django.conf.urls import url

from ecommerce.extensions.payment.views import cybersource, PaymentFailedView, payflow
from ecommerce.extensions.payment.views.paypal import PaypalPaymentExecutionView, PaypalProfileAdminView
from ecommerce.extensions.payment.views.payflow import PayflowPaymentResponseView

urlpatterns = [
    url(r'^cybersource/notify/$', cybersource.CybersourceNotifyView.as_view(), name='cybersource_notify'),
    url(r'^cybersource/redirect/$', cybersource.CybersourceInterstitialView.as_view(), name='cybersource_redirect'),
    url(r'^cybersource/submit/$', cybersource.CybersourceSubmitView.as_view(), name='cybersource_submit'),
    url(r'^error/$', PaymentFailedView.as_view(), name='payment_error'),
    url(r'^paypal/execute/$', PaypalPaymentExecutionView.as_view(), name='paypal_execute'),
    url(r'^paypal/profiles/$', PaypalProfileAdminView.as_view(), name='paypal_profiles'),
    url(r'^payflow/execute/$', PayflowPaymentResponseView.as_view(), name='payflow_execute'),
]
