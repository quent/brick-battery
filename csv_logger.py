"""
Module to log sensors data during the operation.

A CSV file is created or appended to with energy generation, import, A/C
settings and sensors data every time a read is performed in the main loop.
"""

import csv
import logging
import os

LOGGER = logging.getLogger(__name__)

class CSVLogger:
    """
    Simple data logger using a CSV file
    Manually flush on save() to avoid too many commits on SD cards.
    """

    def __init__(self, filename, varlist):
        """
        Pass filename to create/append to, and a list of variable names
        to be used for the header row.
        """
        self.variables = varlist
        if os.path.isfile(filename):
            self.file = open(filename, 'a', newline='')
            self.writer = csv.writer(self.file)
        else:
            self.file = open(filename, 'w', newline='')
            self.writer = csv.writer(self.file)
            self.writer.writerow(self.variables)

    def __del__(self):
        """Close file on destruction"""
        self.file.close()

    def write(self, values_list):
        """Write a new line with data"""
        if len(values_list) != len(self.variables):
            raise ValueError('Invalid number of values in CSVLogger.write: expected %d, got %d' %
                             (len(self.variables), len(values_list)))
        self.writer.writerow(values_list)

    def save(self):
        """Save file to disk"""
        self.file.flush()
