import re
import unicodedata

TABLE = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يك', '01234567890123456789یک')


def normalize(text):
    text = unicodedata.normalize('NFKC', text).translate(TABLE).replace('\u200c', ' ')
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf')
    return '\n'.join(re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines()).strip()
