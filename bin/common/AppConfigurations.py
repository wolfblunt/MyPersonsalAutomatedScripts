"""
    Configuration variables
"""

import configparser
import __root__
import os

# Root path
root_path = __root__.path()
os.chdir('../../..')
# print(os.getcwd())

# Properties file
CONFIGURATION_FILE = "application_conf/settings.conf"

# Config file parser
parser = configparser.RawConfigParser(allow_no_value=True)

parser.read([CONFIGURATION_FILE])

# Mail Settings
sender_email_address = parser.get("MAIL-SETTINGS", "sender_email_address")
receiver_email_address = parser.get("MAIL-SETTINGS", "receiver_email_address")
sender_email_password = parser.get("MAIL-SETTINGS", "sender_email_password")
subject_of_email = "About Pandu & Bothers Company"
body_of_email = "<h1>Please find the attached PDF</h1>"
location_of_file = parser.get("MAIL-SETTINGS", "location_of_file")

# Attachment Details

file_type = parser.get("FILE-SETTINGS", "file_type")
filename = ["AboutCompany.txt", "data1.jpg", "Aman_Resume.pdf"]  # data1.jpg # AboutCompany.txt # Aman_Resume.pdf
attachment_file_directory = parser.get("FILE-SETTINGS", "attachment_file_directory")
