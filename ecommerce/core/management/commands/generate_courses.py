"""
Django management command to generate a test course for a given course id on LMS
"""
import datetime
import json
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from oscar.core.loading import get_model
from waffle.models import Flag

from ecommerce.courses.models import Course

Partner = get_model('partner', 'Partner')
logger = logging.getLogger(__name__)


class Command(BaseCommand):

    help = 'Generate courses on ecommerce from a json list of courses. Should only be run in development environments!'

    valid_seat_types = ["audit", "verified", "honor", "professional", "credit"]
    default_verified_price = 100
    default_professional_price = 1000
    default_credit_price = 200
    default_upgrade_deadline = timezone.now() + datetime.timedelta(days=365)

    def add_arguments(self, parser):
        parser.add_argument(
            'courses',
            help='courses to create in JSON format'  # TODO - link test-course JSON format
        )

    def handle(self, *args, **options):
        # DEBUG is true in development environments and false in production
        if not settings.DEBUG:
            raise CommandError("Command should only be run in development environments")
        try:
            arg = json.loads(options["courses"])
        except ValueError:
            raise CommandError("Invalid JSON object")
        try:
            courses = arg["courses"]
        except KeyError:
            raise CommandError("JSON object is missing courses field")

        partner = Partner.objects.get(short_code='edx')
        site = partner.siteconfiguration.site
        Flag.objects.update_or_create(name='enable_client_side_checkout', defaults={'everyone': True})

        for course_settings in courses:
            # Create the course from course settings
            try:
                module_store = course_settings["store"]
                org = course_settings["organization"]
                num = course_settings["number"]
                run = course_settings["run"]
                course_name = course_settings["fields"]["display_name"]
                if module_store == "split":
                    course_id = "course-v1:{org}+{num}+{run}".format(org=org, num=num, run=run)
                elif module_store == "mongo":
                    course_id = "course-v1:{org}/{num}/{run}".format(org=org, num=num, run=run)
                else:
                    self._abort_course_generation(None, "Modulestore must be one of mongo or split")
                    continue
            except KeyError:
                self._abort_course_generation(None, "Course JSON object is missing required id fields")
                continue

            # Create the course
            defaults = {'name': course_name}
            course, __ = Course.objects.update_or_create(id=course_id, site=site, defaults=defaults)
            msg = "Created course with id %s" % (course.id)
            logger.info(msg)

            # Create seats
            seats = course_settings["seats"]
            seat_types = [seat["seat_type"] for seat in seats]
            if not set(seat_types).issubset(self.valid_seat_types):
                self._abort_course_generation(course, "Seat type must be one of %s" % (self.valid_seat_types))
                continue
            try:
                for seat in course_settings["seats"]:
                    self._create_seat(course, seat, partner)
            except KeyError:
                self._abort_course_generation(course, "Course JSON object is missing required seat fields")
                continue

            # Publish the data to the LMS
            course.publish_to_lms()

    def _abort_course_generation(self, course, error):
        """ Deletes a course having provisioning problems """
        logger.warning("Can't create course, proceeding to next course")
        if course is not None:
            course.delete()
            msg = "Deleted course with id %s" % (course.id)
            logger.info(msg)
        logger.warning(error)

    def _create_seat(self, course, seat, partner):
        """ Add the specified seat to the course """
        seat_type = seat["seat_type"]
        if seat_type == "audit":
            course.create_or_update_seat("", False, 0, partner)
        elif seat_type == "verified":
            # TODO - Enable verification deadline if developers want it
            course.create_or_update_seat(
                "verified",
                True,
                self.default_verified_price,
                partner,
                expires=self.default_upgrade_deadline
            )
        elif seat_type == "honor":
            course.create_or_update_seat("honor", False, 0, partner)
        elif seat_type == "professional":
            # TODO - Enable verification deadline if developers want it
            course.create_or_update_seat(
                "professional",
                seat["id_verification_required"],
                self.default_professional_price,
                partner
            )
        elif seat_type == "credit":
            # Requires manually creating TestCreditProvider in the LMS django admin
            course.create_or_update_seat(
                "credit",
                True,
                self.default_credit_price,
                partner,
                credit_provider="TestCreditProvider",
                expires=self.default_upgrade_deadline,
                credit_hours=1
            )
        msg = "Created %s seat for course %s" % (seat_type, course.id)
        logger.info(msg)
