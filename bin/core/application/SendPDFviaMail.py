from pdf_mail import sendpdf
from bin.common import AppConfigurations


# Taking input of following values
# ex-"abcd@gmail.com"
sender_email_address = AppConfigurations.sender_email_address

# ex-"xyz@gmail.com"
receiver_email_address = AppConfigurations.receiver_email_address

# ex-" abcd1412"
sender_email_password = AppConfigurations.sender_email_password

# ex-"Heading of email"
subject_of_email = AppConfigurations.subject_of_email

# ex-" Matter to be sent"
body_of_email = AppConfigurations.body_of_email

# ex-"Name of file"
filename = AppConfigurations.filename

# ex-"C:/Users / Vasu Gupta/ "
location_of_file = AppConfigurations.location_of_file

# Create an object of sendpdf function
k = sendpdf(sender_email_address,
            receiver_email_address,
            sender_email_password,
            subject_of_email,
            body_of_email,
            filename,
            location_of_file)

# sending an email
k.email_send()
