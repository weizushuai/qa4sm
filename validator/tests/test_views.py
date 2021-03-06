from datetime import datetime, timedelta
import io
import json
import logging
from re import findall as regex_find
from time import sleep
import zipfile

from dateutil.tz import tzlocal
from django.contrib.auth import get_user_model
User = get_user_model()
from django.core import mail
from django.test.testcases import TransactionTestCase
from django.urls.base import reverse
import pytest
from pytz import UTC
from pytz import utc

from valentina.settings import EMAIL_FROM
from validator.models import ValidationRun
from validator.models.dataset import Dataset
from validator.models.filter import DataFilter
from validator.models.settings import Settings
from validator.models.variable import DataVariable
from validator.models.version import DatasetVersion
from validator.urls import urlpatterns
from validator.validation import globals


class TestViews(TransactionTestCase):

    # This re-inits the database for every test, see
    # https://docs.djangoproject.com/en/2.0/topics/testing/overview/#test-case-serialized-rollback
    # It's necessary because the validation view closes the db connection
    # and then the following tests complain about the closed connection.
    # Apparently, re-initing the db creates a new connection every time, so
    # problem solved.
    serialized_rollback = True

    __logger = logging.getLogger(__name__)

    fixtures = ['variables', 'versions', 'datasets', 'filters']

# class TestViews(TestCase):
    def setUp(self):
        self.__logger = logging.getLogger(__name__)

        settings = Settings.load()
        settings.maintenance_mode = False
        settings.save()

        settings.CELERY_TASK_ALWAYS_EAGER = True
        self.credentials = {
            'username': 'testuser',
            'password': 'secret'}

        # second test user
        self.credentials2 = {
            'username': 'seconduser',
            'password': 'shush!',
            'email': 'forgetful@test.com'}

        try:
            self.testuser = User.objects.get(username=self.credentials['username'])
            self.testuser2 = User.objects.get(username=self.credentials2['username'])
        except User.DoesNotExist:
            self.testuser = User.objects.create_user(**self.credentials)
            self.testuser2 = User.objects.create_user(**self.credentials2)

        run_params = {
            'id': '67fc185b-5cd7-4caa-83f0-983e605ede5f',
            'user': self.testuser,
            'start_time': datetime.utcnow().replace(tzinfo=utc) - timedelta(hours=1),
            'end_time': datetime.utcnow().replace(tzinfo=utc),
            'total_points' : 30,
            'error_points' : 5,
        }
        ## Create a test validation run with a specific id so that it can
        ## be accessed via a URL containing that id
        self.testrun = ValidationRun.objects.create(**run_params)
        ## make sure the run's output file name is set:
        self.testrun.output_file.name = str(self.testrun.id) + '/foobar.nc'
        self.testrun.save()

        self.public_views = ['login', 'logout', 'home', 'signup', 'signup_complete', 'terms', 'datasets', 'alpha', 'help', 'about', 'password_reset', 'password_reset_done', 'password_reset_complete', ]
        self.parameter_views = ['result', 'ajax_get_dataset_options', 'password_reset_confirm','stop_validation']
        self.private_views = [p.name for p in urlpatterns if hasattr(p, 'name') and p.name is not None and p.name not in self.public_views and p.name not in self.parameter_views]

    ## Ensure that anonymous access is prevented for private pages
    def test_views_deny_anonymous(self):
        login_url = reverse('login')
        testurls = [ reverse(tv) for tv in self.private_views ]
        testurls.append(reverse('result', kwargs={'result_uuid': self.testrun.id}))

        for url in testurls:
            self.__logger.info(url)
            response = self.client.get(url, follow=True)
            self.assertRedirects(response, '{}?next={}'.format(login_url, url), msg_prefix=url)
            response = self.client.post(url, follow=True)
            self.assertRedirects(response, '{}?next={}'.format(login_url, url), msg_prefix=url)

    ## Check that with valid credentials (set in setUp), access is possible
    def test_views_login(self):
        testurls = [ reverse(tv) for tv in self.private_views ]

        for url in testurls:
            self.client.login(**self.credentials)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

    ## Check that the publicly available views are publicly available anonymously
    def test_public_views(self):
        for pv in self.public_views:
            url = reverse(pv)
            self.__logger.debug("Testing {}".format(url))
            response = self.client.get(url, follow=True)
            self.assertEqual(response.status_code, 200)

    ## Check the results view (with parameter URL)
    def test_result_view(self):
        url = reverse('result', kwargs={'result_uuid': self.testrun.id})
        self.client.login(**self.credentials)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_result(self):
        # create result to delete:
        run = ValidationRun()
        run.user = self.testuser
        run.start_time = datetime.now(tzlocal())
        run.interval_from = datetime(1978, 1, 1, tzinfo=UTC)
        run.interval_to = datetime(2018, 1, 1, tzinfo=UTC)
        run.save()
        result_id=str(run.id)

        assert result_id, "Error saving the test validation run."

        url = reverse('result', kwargs={'result_uuid': result_id})

        # try deleting other user's result - should be blocked
        self.client.login(**self.credentials2)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 403)

        # try to delete own result, should succeed
        self.client.login(**self.credentials)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 200)

        assert not ValidationRun.objects.filter(pk=result_id).exists(), "Validation run didn't get deleted."

    def test_my_results_view(self):
        url = reverse('myruns')
        self.client.login(**self.credentials)
        for i in range(-1, 10):
            response = self.client.get(url, {'page': i})
            self.assertEqual(response.status_code, 200)

        response = self.client.get(url, {'page': 'first'})
        self.assertEqual(response.status_code, 200)

    def test_ajax_get_dataset_options_view(self):
        url = reverse('ajax_get_dataset_options')
        self.client.login(**self.credentials)
        response = self.client.get(url, {'dataset_id': Dataset.objects.get(short_name=globals.GLDAS).id, 'dataset_type': 'ref'})
        self.assertEqual(response.status_code, 200)
        return_data = json.loads(response.content)
        assert return_data['versions']
        assert return_data['variables']

        response = self.client.get(url, {'dataset_id': ''})
        self.assertEqual(response.status_code, 400)

        response = self.client.get(url, {'dataset_id': Dataset.objects.get(short_name=globals.C3S).id, 'dataset_type': 'data'})
        self.assertEqual(response.status_code, 200)
        return_data = json.loads(response.content)
        assert return_data['versions']
        assert return_data['variables']

    ## Submit a validation with minimum parameters set
    def test_submit_validation_min(self):
        url = reverse('validation')
        self.client.login(**self.credentials)
        validation_params = {
            'data_dataset': Dataset.objects.get(short_name=globals.C3S).id,
            'data_version': DatasetVersion.objects.get(short_name=globals.C3S_V201706).id,
            'data_variable': DataVariable.objects.get(short_name=globals.C3S_sm).id,
            'ref_dataset': Dataset.objects.get(short_name=globals.ISMN).id,
            'ref_version': DatasetVersion.objects.get(short_name=globals.ISMN_V20180712_MINI).id,
            'ref_variable': DataVariable.objects.get(short_name=globals.ISMN_soil_moisture).id,
            'scaling_ref': ValidationRun.SCALE_REF,
            'scaling_method': ValidationRun.MEAN_STD,
        }
        result = self.client.post(url, validation_params)
        self.assertEqual(result.status_code, 302)
        self.assertTrue(result.url.startswith('/result/'))

    ## Submit a validation with all possible parameters set
    def test_submit_validation_max(self):
        url = reverse('validation')
        self.client.login(**self.credentials)
        validation_params = {
            'data_dataset': Dataset.objects.get(short_name=globals.C3S).id,
            'data_version': DatasetVersion.objects.get(short_name=globals.C3S_V201706).id,
            'data_variable': DataVariable.objects.get(short_name=globals.C3S_sm).id,
            'ref_dataset': Dataset.objects.get(short_name=globals.ISMN).id,
            'ref_version': DatasetVersion.objects.get(short_name=globals.ISMN_V20180712_MINI).id,
            'ref_variable': DataVariable.objects.get(short_name=globals.ISMN_soil_moisture).id,
            'scaling_ref': ValidationRun.SCALE_REF,
            'scaling_method': ValidationRun.MEAN_STD,
            'filter_data': True,
            'data_filters': DataFilter.objects.get(name='FIL_ALL_VALID_RANGE').id,
            'data_filters': DataFilter.objects.get(name='FIL_C3S_FLAG_0').id,
            'filter_ref': True,
            'ref_filters': DataFilter.objects.get(name='FIL_ALL_VALID_RANGE').id,
            'ref_filters': DataFilter.objects.get(name='FIL_ISMN_GOOD').id,
            'interval_from': datetime(1978,1,1),
            'interval_to': datetime(1998,1,1),
            'name_tag': 'unit test tag so that I can remember my validation',
        }
        result = self.client.post(url, validation_params)
        self.assertEqual(result.status_code, 302)
        self.assertTrue(result.url.startswith('/result/'))

    ## submit a validation with invalid parameters
    def test_submit_validation_invalid(self):
        url = reverse('validation')
        self.client.login(**self.credentials)
        validation_params = {'scaling_ref': 'nosuchthing', 'scaling_method': 'doesnt exist'}
        result = self.client.post(url, validation_params)
        self.assertEqual(result.status_code, 200)

    def test_submit_validation_and_cancel(self):
        start_url = reverse('validation')
        self.client.login(**self.credentials)
        validation_params = {
            'data_dataset': Dataset.objects.get(short_name=globals.C3S).id,
            'data_version': DatasetVersion.objects.get(short_name=globals.C3S_V201706).id,
            'data_variable': DataVariable.objects.get(short_name=globals.C3S_sm).id,
            'ref_dataset': Dataset.objects.get(short_name=globals.GLDAS).id,
            'ref_version': DatasetVersion.objects.get(short_name=globals.GLDAS_TEST).id,
            'ref_variable': DataVariable.objects.get(short_name=globals.GLDAS_SoilMoi0_10cm_inst).id,
            'scaling_ref': ValidationRun.SCALE_REF,
            'scaling_method': ValidationRun.MEAN_STD,
        }
        result = self.client.post(start_url, validation_params)
        self.assertEqual(result.status_code, 302)

        validation_url = result.url

        match = regex_find('/(result)/(.*)/', result.url)
        assert match
        assert match[0]
        assert match[0][0] == 'result'
        assert match[0][1]

        # now let's try out cancelling the validation in it's various forms...
        result_id = match[0][1]
        cancel_url = reverse('stop_validation', kwargs={'result_uuid': result_id})

        # check that the cancel url does something even if we're not DELETEing
        self.client.login(**self.credentials2)
        result = self.client.get(cancel_url)

        # check that nobody but the owner can cancel the validation
        result = self.client.delete(cancel_url)
        self.assertEqual(result.status_code, 403)

        # check that the owner can cancel it
        self.client.login(**self.credentials)
        result = self.client.delete(cancel_url)
        self.assertEqual(result.status_code, 200)

        # after cancelling, we still get a result for the validation
        result = self.client.get(validation_url)
        self.assertEqual(result.status_code, 200)

    ## Stress test the server!
    @pytest.mark.long_running
    def no_test_submit_lots_of_validations(self): # deactivate the test until we found out what makes it hang
        N = 10
        timeout = 300 # seconds
        wait_time = 5 # seconds

        url = reverse('validation')
        self.client.login(**self.credentials)

        validation_params = {
            'data_dataset': Dataset.objects.get(short_name=globals.C3S).id,
            'data_version': DatasetVersion.objects.get(short_name=globals.C3S_V201706).id,
            'data_variable': DataVariable.objects.get(short_name=globals.C3S_sm).id,
            'ref_dataset': Dataset.objects.get(short_name=globals.ISMN).id,
            'ref_version': DatasetVersion.objects.get(short_name=globals.ISMN_V20180712_MINI).id,
            'ref_variable': DataVariable.objects.get(short_name=globals.ISMN_soil_moisture).id,
            'scaling_ref': ValidationRun.SCALE_REF,
            'scaling_method': ValidationRun.MEAN_STD,
            'filter_data': True,
            'data_filters': DataFilter.objects.get(name='FIL_ALL_VALID_RANGE').id,
            'data_filters': DataFilter.objects.get(name='FIL_C3S_FLAG_0').id,
            'filter_ref': True,
            'ref_filters': DataFilter.objects.get(name='FIL_ALL_VALID_RANGE').id,
            'ref_filters': DataFilter.objects.get(name='FIL_ISMN_GOOD').id,
        }

        result_urls = {}

        self.__logger.info('Starting {} validations from the view...'.format(N))

        ## start lots of validations
        for idx in range(N):
            result = self.client.post(url, validation_params)
            self.assertEqual(result.status_code, 302)

            ## make sure we're redirected to a results page and remember which
            match = regex_find('/(result)/(.*)/', result.url)
            assert match
            assert match[0]
            assert match[0][0] == 'result'
            assert match[0][1]
            result_urls[match[0][1]] = result.url

        ## wait until the validations are finished
        finished_jobs = 0
        runtime = 0
        while finished_jobs < N:
            assert runtime <= timeout, 'Validations are taking too long.'
            self.__logger.info("Validations not finished yet... ({} done)".format(finished_jobs))
            finished_jobs = 0
            # wait a bit and keep track of time
            sleep(wait_time)
            runtime += wait_time
            for uuid, url in result_urls.items():
                validation_run = ValidationRun.objects.get(pk=uuid)
                if validation_run.end_time:
                    finished_jobs += 1

        self.__logger.info("Validations finished now!")

        ## now check the results views for all validations
        for uuid, url in result_urls.items():
            result = self.client.get(url, follow=True)
            self.assertEqual(result.status_code, 200)

            content = result.content.decode('utf-8')

            # make sure there are the expected html elements for a successful validation
            assert content.find('id="result_summary"'), 'No summary'
            assert content.find('id="id_graph_box"'), 'No graphs'
            assert content.find('id="netcdf_box"'), 'No NetCDF download'

            # find the links to the graphs zip and netcdf file
            netcdf_match = regex_find("href='([^']*.nc)'", content)
            zip_match = regex_find("href='([^']*.zip)'", content)
            assert netcdf_match[0], 'No netcdf link found'
            assert zip_match[0], 'No graphs zip link found'

            # check that we can download the graphs zip and netcdf file
            zip_result = self.client.get(zip_match[0], follow=True)
            netcdf_result = self.client.get(netcdf_match[0], follow=True)
            assert zip_result
            assert netcdf_result
            assert zip_result.get('Content-Type') == 'application/zip', 'Wrong mimetype for zip'
            assert netcdf_result.get('Content-Type') == 'application/x-netcdf', 'Wrong mimetype for netcdf'

            # check contents of zipfile
            zip_content = io.BytesIO(b"".join(zip_result.streaming_content))
            zip_file = zipfile.ZipFile(zip_content, 'r')
            self.assertIsNone(zip_file.testzip(), 'Graph zipfile is corrupt')
            assert len(zip_file.namelist()) > 0, 'Nothing in the zipfile'

            # check netcdf file
            netcdf_stream = io.BytesIO(b"".join(netcdf_result.streaming_content))
            assert netcdf_stream, 'netcdf file corrupt'

    def test_access_to_results(self):
        self.client.login(**self.credentials2)

        # try to access other test users result
        url = reverse('result', kwargs={'result_uuid': self.testrun.id})
        response = self.client.get(url)

        # we should be able to see it
        self.assertEqual(response.status_code, 200)

    ## try signing up a new user with all fields given
    def test_signup_new_user_full(self):
        url = reverse('signup')
        user_info = {
            'username': 'chuck_norris',
            'password1': 'Fae6eij7NuoY5Fa1thii',
            'password2': 'Fae6eij7NuoY5Fa1thii',
            'email': 'chuck@norris.com',
            'first_name': 'Chuck',
            'last_name': 'Norris',
            'organisation': 'Texas Rangers',
            'organisation': 'United States of America',
            'terms_consent': True,
            }
        result = self.client.post(url, user_info)
        self.assertEqual(result.status_code, 302)
        self.assertRedirects(result, reverse('signup_complete'))

    ## try signing up a new user and giving only the required fields
    def test_signup_new_user_minimal(self):
        url = reverse('signup')
        user_info = {
            'username': 'chuck_norris',
            'password1': 'Fae6eij7NuoY5Fa1thii',
            'password2': 'Fae6eij7NuoY5Fa1thii',
            'email': 'chuck@norris.com',
            'terms_consent': True,
            }
        result = self.client.post(url, user_info)
        self.assertEqual(result.status_code, 302)
        self.assertRedirects(result, reverse('signup_complete'))

    ## make sure the user has to check the terms consent box
    def test_signup_new_user_no_consent(self):
        url = reverse('signup')
        user_info = {
            'username': 'chuck_norris',
            'password1': 'Fae6eij7NuoY5Fa1thii',
            'password2': 'Fae6eij7NuoY5Fa1thii',
            'email': 'chuck@norris.com',
            'terms_consent': False,
            }
        result = self.client.post(url, user_info)
        self.assertEqual(result.status_code, 200)

    ## simulate workflow for password reset
    def test_password_reset(self):
        ## pattern to get the password reset link from the email
        reset_url_pattern = reverse('password_reset_confirm', kwargs={'uidb64': 'DUMMY', 'token': 'DUMMY'})
        reset_url_pattern = reset_url_pattern.replace('DUMMY', '([^/]+)')

        orig_password = self.credentials2['password']

        ## go to the password reset page
        url = reverse('password_reset')
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)

        ## send it the user's email
        response = self.client.post(url, {'email': self.credentials2['email'], })
        self.assertRedirects(response, reverse('password_reset_done'))

        ## make sure the right email got sent with correct details
        sent_mail = mail.outbox[0]
        assert sent_mail
        assert sent_mail.subject
        assert sent_mail.body
        assert sent_mail.from_email == EMAIL_FROM
        assert self.credentials2['email'] in sent_mail.to
        assert self.credentials2['username'] in sent_mail.body

        ## check that the email contains a confirmation link with userid and token
        urls = regex_find(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', sent_mail.body)
        userid = None
        token = None
        for u in urls:
            rmatch = regex_find(reset_url_pattern, u)
            if rmatch:
                userid = rmatch[0][0]
                token = rmatch[0][1]

        assert userid
        assert token

        ## now try to use the link in the email several times - should only be successful the first time
        for i in range(1, 3):
            ## go to the confirmation link given in the email
            url = reverse('password_reset_confirm', kwargs={'uidb64': userid, 'token': token})
            response = self.client.get(url)

            ## first time
            if i == 1:
                self.assertEqual(response.status_code, 302)
                ## follow redirect and enter new password - we should be redirected to password_reset_complete
                url = response.url
                self.credentials2['password'] = '1superPassword!!'
                response = self.client.post(url, {'new_password1': self.credentials2['password'], 'new_password2': self.credentials2['password'],})
                self.assertRedirects(response, reverse('password_reset_complete'))
            ## second time
            else:
                ## no redirect and message that reset wasn't successful
                self.assertEqual(response.status_code, 200)
                assert response.context['title'] == 'Password reset unsuccessful'

        ## make sure we can log in with the new password
        login_success = self.client.login(**self.credentials2)
        assert login_success

        ## make sure we can't log in with the old password
        login_success = self.client.login(**{'username': self.credentials2['username'], 'password': orig_password })
        assert not login_success
