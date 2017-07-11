import ddt
import httpretty
import mock
from django.core.management import CommandError, call_command
from django.test import override_settings

from ecommerce.courses.models import Course
from ecommerce.extensions.catalogue.tests.mixins import CourseCatalogTestMixin
from ecommerce.tests.testcases import TestCase


@ddt.ddt
class GenerateCoursesTests(CourseCatalogTestMixin, TestCase):

    default_verified_price = 100
    default_professional_price = 1000
    default_credit_price = 200

    def test_invalid_env(self):
        """
        Test that running the command in a non-development environment will raise the appropriate command error
        """
        msg = "Command should only be run in development environments"
        with self.assertRaisesRegexp(CommandError, msg):
            arg = 'arg'
            call_command("generate_courses", arg)

    @override_settings(DEBUG=True)
    def test_invalid_json(self):
        """
        Test that providing an invalid JSON object will raise the appropriate command error
        """
        msg = "Invalid JSON object"
        with self.assertRaisesRegexp(CommandError, msg):
            arg = 'invalid_json'
            call_command("generate_courses", arg)

    @override_settings(DEBUG=True)
    def test_missing_courses_field(self):
        """
        Test that missing the courses key will raise the appropriate command error
        """
        msg = "JSON object is missing courses field"
        with self.assertRaisesRegexp(CommandError, msg):
            arg = ('{}')
            call_command("generate_courses", arg)

    @override_settings(DEBUG=True)
    @mock.patch('ecommerce.core.management.commands.generate_courses.logger')
    def test_missing_id_fields(self, mock_logger):
        """
        Test that missing id fields in course JSON will result in the appropriate log messages
        """
        msg = "Course JSON object is missing required id fields"
        arg = (
            '{"courses":[{' +
            '"store":"split",' +
            '"organization":"test-course-generator"}]}'
        )
        call_command("generate_courses", arg)
        mock_logger.warning.assert_any_call(msg)

    @override_settings(DEBUG=True)
    @mock.patch('ecommerce.core.management.commands.generate_courses.logger')
    def test_invalid_store(self, mock_logger):
        """
        Test that providing an invalid store option will result in the appropriate log messages
        """
        msg = "Modulestore must be one of mongo or split"
        arg = (
            '{"courses":[{' +
            '"store":"invalid_store",' +
            '"organization":"test-course-generator",' +
            '"number":"1",' +
            '"run":"1",' +
            '"seats":[],' +
            '"fields":{"display_name":"test-course"}}]}'
        )
        call_command("generate_courses", arg)
        mock_logger.warning.assert_any_call(msg)

    @override_settings(DEBUG=True)
    @mock.patch('ecommerce.core.management.commands.generate_courses.logger')
    def test_missing_seat_fields(self, mock_logger):
        """
        Test that missing seat fields in course JSON will result in the appropriate log messages
        """
        msg = "Course JSON object is missing required seat fields"
        arg = (
            '{"courses":[{' +
            '"store":"split",' +
            '"organization":"test-course-generator",' +
            '"number":"1",' +
            '"run":"1",' +
            '"seats":[{"seat_type":"professional"}],' +
            '"fields":{"display_name":"test-course"}}]}'
        )
        call_command("generate_courses", arg)
        mock_logger.warning.assert_any_call(msg)

    @override_settings(DEBUG=True)
    @mock.patch('ecommerce.core.management.commands.generate_courses.logger')
    def test_invalid_seat_type(self, mock_logger):
        """
        Test that an invalid seat type in course JSON will result in the appropriate log messages
        """
        valid_seat_types = ["audit", "verified", "honor", "professional", "credit"]
        msg = "Seat type must be one of {}".format(valid_seat_types)
        arg = (
            '{"courses":[{' +
            '"store":"split",' +
            '"organization":"test-course-generator",' +
            '"number":"1",' +
            '"run":"1",' +
            '"seats":[{"seat_type":"invalid_seat_type"}],' +
            '"fields":{"display_name":"test-course"}}]}'
        )
        call_command("generate_courses", arg)
        mock_logger.warning.assert_any_call(msg)

    @override_settings(DEBUG=True)
    @httpretty.activate
    @mock.patch('ecommerce.core.management.commands.generate_courses.logger')
    @ddt.data("audit", "honor", "verified", "professional", "credit")
    def test_create_seat(self, seat_type, mock_logger):
        """
        The command should create the demo course with a seat,
        and publish that data to the LMS.
        """
        if seat_type == "verified":
            price = self.default_verified_price
        elif seat_type == "professional":
            price = self.default_professional_price
        elif seat_type == "credit":
            price = self.default_credit_price
        else:
            price = 0

        self.mock_access_token_response()
        arg = (
            '{"courses":[{' +
            '"store":"split",' +
            '"organization":"test-course-generator",' +
            '"number":"1",' +
            '"run":"1",' +
            '"seats":[{"seat_type":"' + seat_type + '","id_verification_required":false}],' +
            '"fields":{"display_name":"test-course"}}]}'
        )
        with mock.patch.object(Course, 'publish_to_lms', return_value=None) as mock_publish:
            call_command('generate_courses', arg)
            mock_publish.assert_called_once_with()

        course = Course.objects.get(id='course-v1:test-course-generator+1+1')
        seats = course.seat_products
        seat = seats[0]
        self.assertEqual(seat.stockrecords.get(partner=self.partner).price_excl_tax, price)
        mock_logger.info.assert_any_call(
            'Created {seat_type} seat for course {course_id}'.format(
                seat_type=seat_type,
                course_id='course-v1:test-course-generator+1+1'
            )
        )
