from unittest import mock

import requests_mock
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_dynamic_fixture import get
from djstripe import models as djstripe
from djstripe.enums import SubscriptionStatus

from readthedocs.organizations.models import Organization
from readthedocs.subscriptions.models import Plan, Subscription


@override_settings(RTD_ALLOW_ORGANIZATIONS=True)
class SubscriptionViewTests(TestCase):

    """Subscription view tests."""

    def setUp(self):
        self.user = get(User)
        self.organization = get(Organization, stripe_id='123', owners=[self.user])
        self.plan = get(Plan, published=True, slug=settings.ORG_DEFAULT_SUBSCRIPTION_PLAN_SLUG)
        self.subscription = get(
            Subscription,
            organization=self.organization,
            plan=self.plan,
            status='active',
        )
        self.client.force_login(self.user)

    def test_active_subscription(self):
        resp = self.client.get(reverse('subscription_detail', args=[self.organization.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['subscription'], self.subscription)
        self.assertContains(resp, 'active')
        # The subscribe form isn't shown, but the manage susbcription button is.
        self.assertContains(resp, 'Manage Subscription')
        self.assertNotContains(resp, 'Create Subscription')

    @requests_mock.Mocker(kw='mock_request')
    def test_manage_subscription(self, mock_request):
        payload = {
            'url': 'https://billing.stripe.com/session/a1b2c3',
        }
        mock_request.post('https://api.stripe.com/v1/billing_portal/sessions', json=payload)
        response = self.client.post(
            reverse(
                'stripe_customer_portal',
                kwargs={'slug': self.organization.slug},
            ),
        )
        self.assertRedirects(
            response,
            payload.get('url'),
            fetch_redirect_response=False,
        )

    @mock.patch(
        "readthedocs.subscriptions.utils.stripe.Customer.modify", new=mock.MagicMock
    )
    @mock.patch("readthedocs.subscriptions.utils.djstripe.Customer._get_or_retrieve")
    @mock.patch("readthedocs.subscriptions.utils.stripe.Customer.create")
    def test_user_without_subscription(
        self, customer_create_mock, customer_retrieve_mock
    ):
        stripe_customer = get(
            djstripe.Customer,
            id="cus_a1b2c3",
        )
        stripe_subscription = get(
            djstripe.Subscription,
            id="sub_a1b2c3",
            start_date=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
            trial_end=timezone.now() + timezone.timedelta(days=30),
            status=SubscriptionStatus.active,
            customer=stripe_customer,
        )
        stripe_customer.subscribe = mock.MagicMock()
        stripe_customer.subscribe.return_value = stripe_subscription
        customer_retrieve_mock.return_value = stripe_customer

        self.subscription.delete()
        self.organization.refresh_from_db()
        self.assertFalse(hasattr(self.organization, 'subscription'))
        self.assertIsNone(self.organization.stripe_customer)

        resp = self.client.get(reverse('subscription_detail', args=[self.organization.slug]))
        self.assertEqual(resp.status_code, 200)
        self.organization.refresh_from_db()
        subscription = self.organization.subscription
        self.assertEqual(subscription.status, 'active')
        self.assertEqual(subscription.stripe_id, 'sub_a1b2c3')
        self.assertEqual(self.organization.stripe_customer, stripe_customer)
        customer_retrieve_mock.assert_called_once()
        customer_create_mock.assert_not_called()

    @mock.patch(
        "readthedocs.subscriptions.utils.djstripe.Customer.sync_from_stripe_data"
    )
    @mock.patch("readthedocs.subscriptions.utils.djstripe.Customer._get_or_retrieve")
    @mock.patch("readthedocs.subscriptions.utils.stripe.Customer.create")
    def test_user_without_subscription_and_customer(
        self, customer_create_mock, customer_retrieve_mock, sync_from_stripe_data_mock
    ):
        stripe_customer = get(
            djstripe.Customer,
            id="cus_a1b2c3",
        )
        stripe_subscription = get(
            djstripe.Subscription,
            id="sub_a1b2c3",
            start_date=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
            trial_end=timezone.now() + timezone.timedelta(days=30),
            status=SubscriptionStatus.active,
            customer=stripe_customer,
        )
        stripe_customer.subscribe = mock.MagicMock()
        stripe_customer.subscribe.return_value = stripe_subscription
        customer_retrieve_mock.return_value = None
        sync_from_stripe_data_mock.return_value = stripe_customer

        # When stripe_id is None, a new customer is created.
        self.organization.stripe_id = None
        self.organization.save()
        self.subscription.delete()
        self.organization.refresh_from_db()
        self.assertFalse(hasattr(self.organization, 'subscription'))
        self.assertIsNone(self.organization.stripe_id)
        self.assertIsNone(self.organization.stripe_customer)

        customer_retrieve_mock.reset_mock()
        resp = self.client.get(reverse('subscription_detail', args=[self.organization.slug]))
        self.assertEqual(resp.status_code, 200)
        self.organization.refresh_from_db()
        subscription = self.organization.subscription
        self.assertEqual(subscription.status, 'active')
        self.assertEqual(subscription.stripe_id, 'sub_a1b2c3')
        self.assertEqual(self.organization.stripe_id, 'cus_a1b2c3')
        self.assertEqual(self.organization.stripe_customer, stripe_customer)
        customer_create_mock.assert_called_once()
        customer_retrieve_mock.assert_not_called()

    def test_user_with_canceled_subscription(self):
        self.subscription.status = 'canceled'
        self.subscription.save()
        resp = self.client.get(reverse('subscription_detail', args=[self.organization.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['subscription'], self.subscription)
        # The Manage Subscription form isn't shown, but the Subscribe is.
        self.assertNotContains(resp, 'Manage Subscription')
        self.assertContains(resp, 'Create Subscription')
