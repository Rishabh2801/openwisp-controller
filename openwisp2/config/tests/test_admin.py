import json

from django.contrib.auth.models import Permission
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse

from openwisp2.users.models import OrganizationUser, User

from . import CreateAdminMixin, CreateConfigTemplateMixin, TestVpnX509Mixin
from ...pki.models import Ca, Cert
from ...tests import TestOrganizationMixin
from ..models import Config, Template, Vpn


class TestAdmin(CreateConfigTemplateMixin, CreateAdminMixin,
                TestVpnX509Mixin, TestOrganizationMixin, TestCase):
    """
    tests for Config model
    """
    ca_model = Ca
    cert_model = Cert
    config_model = Config
    template_model = Template
    vpn_model = Vpn
    operator_permissions = Permission.objects.filter(Q(codename__endswith='config') |
                                                     Q(codename__endswith='template'))

    def setUp(self):
        super(TestAdmin, self).setUp()
        self._login()

    def _create_operator(self, permissions=operator_permissions, organizations=[]):
        operator = User.objects.create_user(username='operator',
                                            password='tester',
                                            email='operator@test.com',
                                            is_staff=True)
        for perm in permissions:
            operator.user_permissions.add(perm)
        for organization in organizations:
            OrganizationUser.objects.create(user=operator, organization=organization)
        return operator

    def test_config_and_template_different_organization(self):
        org1 = self._create_org()
        template = self._create_template(organization=org1)
        org2 = self._create_org(name='test org2', slug='test-org2')
        config = self._create_config(organization=org2)
        path = reverse('admin:config_config_change', args=[config.pk])
        # ensure it fails with error
        response = self.client.post(path, {'templates': str(template.pk), 'key': self.TEST_KEY})
        self.assertIn('errors field-templates', str(response.content))
        # remove conflicting template and ensure doesn't error
        response = self.client.post(path, {'templates': '', 'key': self.TEST_KEY})
        self.assertNotIn('errors field-templates', str(response.content))

    def test_preview_config(self):
        org = self._create_org()
        self._create_template(organization=org)
        templates = Template.objects.all()
        path = reverse('admin:config_config_preview')
        config = json.dumps({
            'interfaces': [
                {
                    'name': 'eth0',
                    'type': 'ethernet',
                    'addresses': [
                        {
                            'family': 'ipv4',
                            'proto': 'dhcp'
                        }
                    ]
                }
            ]
        })
        data = {
            'name': 'test-config',
            'organization': org.pk,
            'mac_address': self.TEST_MAC_ADDRESS,
            'backend': 'netjsonconfig.OpenWrt',
            'config': config,
            'csrfmiddlewaretoken': 'test',
            'templates': ','.join([str(t.pk) for t in templates])
        }
        response = self.client.post(path, data)
        self.assertContains(response, '<pre class="djnjc-preformatted')
        self.assertContains(response, 'eth0')
        self.assertContains(response, 'dhcp')

    def _create_multitenancy_test_env(self):
        org1 = self._create_org(name='test1org')
        org2 = self._create_org(name='test2org')
        inactive = self._create_org(name='inactive-org', is_active=False)
        operator = self._create_operator(organizations=[org1, inactive])
        t1 = self._create_template(name='template1org', organization=org1)
        t2 = self._create_template(name='template2org', organization=org2)
        t3 = self._create_template(name='t3-inactive', organization=inactive)
        c1 = self._create_config(name='org1-config', organization=org1)
        c2 = self._create_config(name='org2-config',
                                 organization=org2,
                                 key=None,
                                 mac_address='00:11:22:33:44:56')
        c3 = self._create_config(name='config-inactive',
                                 organization=inactive,
                                 key=None,
                                 mac_address='00:11:22:33:44:57')
        c1.templates.add(t1)
        c2.templates.add(t2)
        return dict(c1=c1, c2=c2, c3_inactive=c3,
                    t1=t1, t2=t2, t3_inactive=t3,
                    org1=org1, org2=org2,
                    inactive=inactive,
                    operator=operator)

    def _test_multitenant_admin(self, url, visible, hidden,
                                select_widget=False):
        """
        reusable test function that ensures different users
        can see the right objects.
        an operator with limited permissions will not be able
        to see the elements contained in ``hidden``, while
        a superuser can see everything.
        """
        self._logout()
        self._login(username='operator', password='tester')
        response = self.client.get(url)

        # utility format function
        def _f(el, select_widget=False):
            if select_widget:
                return '{0}</option>'.format(el)
            return el

        # ensure elements in visible list are visible to operator
        for el in visible:
            self.assertContains(response, _f(el, select_widget),
                                msg_prefix='[operator contains]')
        # ensure elements in hidden list are not visible to operator
        for el in hidden:
            self.assertNotContains(response, _f(el, select_widget),
                                   msg_prefix='[operator not-contains]')

        # now become superuser
        self._logout()
        self._login(username='admin', password='tester')
        response = self.client.get(url)
        # ensure all elements are visible to superuser
        all_elements = visible + hidden
        for el in all_elements:
            self.assertContains(response, _f(el, select_widget),
                                msg_prefix='[superuser contains]')

    def test_config_queryset(self):
        data = self._create_multitenancy_test_env()
        self._test_multitenant_admin(
            url=reverse('admin:config_config_changelist'),
            visible=[data['c1'].name],
            hidden=[data['c2'].name, data['c3_inactive'].name]
        )

    def test_config_organization_fk_queryset(self):
        data = self._create_multitenancy_test_env()
        self._test_multitenant_admin(
            url=reverse('admin:config_config_add'),
            visible=[data['org1'].name],
            hidden=[data['org2'].name, data['inactive'].name],
            select_widget=True
        )

    def test_config_templates_m2m_queryset(self):
        data = self._create_multitenancy_test_env()
        t_shared = self._create_template(name='t-shared',
                                         organization=None)
        self._test_multitenant_admin(
            url=reverse('admin:config_config_add'),
            visible=[str(data['t1']), str(t_shared)],
            hidden=[str(data['t2']), str(data['t3_inactive'])],
        )

    def test_template_queryset(self):
        data = self._create_multitenancy_test_env()
        self._test_multitenant_admin(
            url=reverse('admin:config_template_changelist'),
            visible=[data['t1'].name],
            hidden=[data['t2'].name, data['t3_inactive'].name],
        )

    def test_template_organization_fk_queryset(self):
        data = self._create_multitenancy_test_env()
        self._test_multitenant_admin(
            url=reverse('admin:config_template_add'),
            visible=[data['org1'].name],
            hidden=[data['org2'].name, data['inactive'].name],
            select_widget=True
        )

    def test_template_vpn_fk_queryset(self):
        data = self._create_multitenancy_test_env()
        vpn1 = self._create_vpn(name='vpn1org', organization=data['org1'])
        vpn2 = self._create_vpn(name='vpn2org', organization=data['org2'])
        vpn3 = self._create_vpn(name='vpn3shared', organization=None)
        vpn4 = self._create_vpn(name='vpn4inactive', organization=data['inactive'])
        data['t1'].type = 'vpn-client'
        data['t1'].vpn = vpn1
        data['t1'].save()
        self._test_multitenant_admin(
            url=reverse('admin:config_template_add'),
            visible=[vpn1.name, vpn3.name],
            hidden=[vpn2.name, vpn4.name],
            select_widget=True
        )
