import smtplib
import os
from email.message import EmailMessage
# For guessing MIME type based on file name extension
import mimetypes
from bin.common import AppConfigurations

# import bin.core.templates as template

senders_mail = AppConfigurations.sender_email_address


def send_mail(receiver_address):
    try:
        message = EmailMessage()
        message["subject"] = AppConfigurations.subject_of_email
        message["from"] = senders_mail
        message["to"] = receiver_address
        message.set_content("This is my fist Test Mail")
        # os.chdir('../..')
        print(os.getcwd())
        html_content = open('bin/core/templates/sampleTemplate.html').read()
        print("html_content : ", html_content)
        message.add_alternative(html_content, subtype="html")
        # file_data, file_name, file_type = add_attachment()
        # message.add_attachment(file_data, maintype="pdf", file_name=file_name)
        message = add_pdf_attachment(message)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(senders_mail, AppConfigurations.sender_email_password)
            smtp.send_message(message)

        print("Mail sent successfully to {}".format(receiver_address))

    except Exception as e:
        print(str(e))
        raise Exception


def add_pdf_attachment(message):
    try:
        print(os.getcwd())
        for filename in os.listdir(AppConfigurations.attachment_file_directory):
            print("Loop Dir", os.getcwd())
            print("filename : ", filename)
            if filename in AppConfigurations.filename:
                path = os.path.join(AppConfigurations.attachment_file_directory, filename)
                if not os.path.isfile(path):
                    continue
                # Guess the content type based on the file's extension.  Encoding
                # will be ignored, although we should check for simple things like
                # gzip'd or compressed files.
                ctype, encoding = mimetypes.guess_type(path)
                if ctype is None or encoding is not None:
                    # No guess could be made, or the file is encoded (compressed), so
                    # use a generic bag-of-bits type.
                    ctype = 'application/octet-stream'
                print("ctype : ", ctype)
                maintype, subtype = ctype.split('/', 1)
                print("maintype : ", maintype)
                print("subtype : ", subtype)
                with open(path, 'rb') as fp:
                    message.add_attachment(fp.read(), maintype=maintype, subtype=subtype, filename=filename)

        return message
    except Exception as e:
        print(str(e))
        raise Exception


send_mail(AppConfigurations.receiver_email_address)
