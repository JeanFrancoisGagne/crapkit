"""CRLF line endings throughout."""


def normalize(text, strip):
    if strip:
        text = text.strip()
    if not text:
        return None
    return text.lower()


def count_words(text):
    words = 0
    for part in text.split():
        if part.isalpha():
            words += 1
    return words
