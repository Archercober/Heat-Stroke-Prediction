#!/usr/bin/env python
'''
prediction_handler.py

This script implements a class called PredictoinHandler.py which contains a MonitorUser, 
HeatStrokeMonitor, and HeatStrokePredictor object. Instances of this class instantiate these
objects and couriers data between them to get and report predictions of heat stroke risk.
'''

import os
import time
import threading
import logging
import warnings

import pandas as pd
import numpy as np

import emoji
import coloredlogs
from termcolor import colored

import user
import monitor
import predictor

coloredlogs.install(level='DEBUG')
logger = logging.getLogger(__name__)

__author__ = "Jon Deaton"
__email__ = "jdeaton@stanford.edu"

class LoopingThread(threading.Timer):
    # This is a thread that performs some action
    # repeatedly at a given interval. Since this
    # interval may be long, this 

    def __init__(self, callback, wait_time):
        threading.Thread.__init__(self)
        self.callback = callback
        self.wait_time = wait_time

        self.loop_wait = 1 if wait_time > 1 else wait_time
        self.num_loops = int(wait_time / self.loop_wait)

        self._is_running = True

    def run(self):
        while self._is_running:
            for _ in range(self.num_loops):
                # Check to make sure the thread should still be running
                if not self._is_running: return
                time.sleep(self.loop_wait)
            self.callback()

    def stop(self):
        self._is_running = False

class PredictionHandler(object):

    def __init__(self, users_XML="users.xml", username=None, output_dir=None):
        
        logger.debug("Instantiating user...")
        self.user = user.MonitorUser(users_XML=users_XML, load=True, username=username)
        logger.info(emoji.emojize("Monitor user: %s %s" % (self.user.name, self.user.emoji)))

        logger.debug("Instantiating monitor...")
        self.monitor = monitor.HeatStrokeMonitor()
        logger.debug(emoji.emojize("Monitor instantiated :heavy_check_mark:"))

        logger.debug("Instantiating predictor...")
        self.predictor = predictor.HeatStrokePredictor()
        logger.debug(emoji.emojize("Predictor instantiated :heavy_check_mark:"))
        
        self.current_fields = self.user.series.keys()
        self.user_fields = ['Age', 'Sex', 'Weight (kg)', 'BMI', 'Height (cm)',
                             'Nationality', 'Cardiovascular disease history',
                             'Sickle Cell Trait (SCT)'] 

        # Allocate a risk series for a risk estimate time series
        self.risk_series = pd.Series()
        self.CT_risk_series = pd.Series()
        self.HI_risk_series = pd.Series()
        self.LR_risk_series = pd.Series()

        # Set the output directory and save files
        # Make a directory to contain the files if one doesn't already exist
        if output_dir and not os.path.isdir(output_dir): os.mkdir(output_dir)
        
        # Set the output directory to be the data directory if one was not provided
        current_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        self.output_dir = output_dir if output_dir else current_dir

        # Set the output file paths inside of the output directory
        self.risk_csv_file = os.path.join(self.output_dir, "risk_series.csv")
        self.data_save_file = os.path.join(self.output_dir, "all_data.csv")

    def initialize_threads(self, test=False):
        # Make a threading object to collect the data
        self.monitor.set_threading_class(test=test)

        # Make a thread that preiodically makes a risk prediction
        self.prediciton_thread = LoopingThread(self.make_prediction, 5)

        # Make a thread that periodically saves all the data
        self.saving_thread = LoopingThread(self.save_all_data, 30)

    def start_data_collection(self):
        # This function initiates a thread (handled by HeatStrokeMonitor)
        # that will continuously try to read and parse data from the Serial port
        self.monitor.read_data_from_port()

    def start_prediction_thread(self):
        # Start the prediction thread
        self.prediciton_thread.start()

    def stop_prediction_thread(self):
        # For starting the predicont thread
        self.prediciton_thread.stop()

    def get_current_attributes(self):
        # This function gets data from the MonitorUser instantiation and formats it in a way
        #logger.warning("get_current_attributes not implemented!")
        user_attributes = self.user.get_user_attributes()

        # We need to loop through all of the differen data streams coming from the monitor
        # and store the most recent value in ihe user's attributes
        # This dictionary provides a mapping from user attribute field name to
        # the relevant field
        stream_dict = {
        'Heart / Pulse rate (b/min)': self.monitor.HR_stream, 
        'Environmental temperature (C)': self.monitor.ETemp_stream,
        'Relative Humidity': self.monitor.EHumid_stream,
        'Skin Temperature': self.monitor.STemp_stream,
        'Sweating': self.monitor.GSR_stream,
        'Acceleration': self.monitor.Acc_stream,
        'Skin color (flushed/normal=1, pale=0.5, cyatonic=0)': self.monitor.Skin_stream
        }

        # Loop through all the streams and add the most recent value to user_attributes
        for field in stream_dict:
            stream = stream_dict[field]
            user_attributes.set_value(field, stream.iloc[-1])

        user_attributes.set_value('Exposure to sun', 0)

        return user_attributes

    def make_prediction(self):
        # This funciton makes a prediction
        try:
            user_attributes = self.get_current_attributes()
        except:
            logger.error("Not enough data to make a predictoin.")
            return

        # Calculate the risk!!!
        tup = self.predictor.make_prediction(user_attributes, self.monitor.HR_stream, self.monitor.STemp_stream, each=True)
        CT_prob, HI_prob, LR_prob, risk = tup

        # Record the time that the risk assessment was made, and save it to the series
        now = time.time()
        self.risk_series.set_value(now, risk)
        self.CT_risk_series.set_value(now, CT_prob)
        self.HI_risk_series.set_value(now, HI_prob)
        self.LR_risk_series.set_value(now, LR_prob)

        # Log the risk to terminal
        logger.info(colored("CT Risk: %.4f %s" % (CT_prob, progress_bar(CT_prob)), "yellow"))
        logger.info(colored("HI Risk: %.4f %s" % (HI_prob, progress_bar(HI_prob)), "yellow"))
        logger.info(colored("LR Risk: %.4f %s" % (LR_prob, progress_bar(LR_prob)), "yellow"))
        bar = progress_bar(risk, filler=":fire: ")
        logger.info(colored(emoji.emojize("Current risk: %.4f %s" % (risk, bar)), 'red'))
        
    def stop_all_threads(self, wait=False):
        # This function sends a stop signal to all threads
        # The optional wait parameter indicates whether this function should wait to return until it is sure
        # that all of the treats have stopped running
        self.stop_prediction_thread()
        self.monitor.stop_data_read()
        self.saving_thread.stop()

        if wait:
            logger.debug("Waiting for threads to die...")
            while True:
                try:
                    while threading.activeCount() > 1: time.sleep(0.1)
                    break
                except KeyboardInterrupt:
                    continue

            logger.debug("Threads died. Thread count: %d" % threading.activeCount())

    def save_all_data(self):
        # This saves all the recorded data including risk estimates
        logger.debug("Saving all data to: %s ..." % os.path.basename(self.data_save_file))

        df = self.monitor.get_compiled_df()
        core_temperature_series = self.predictor.estimate_core_temperature(self.monitor.HR_stream, 37.6)

        # The dataframe returned by the monitor not be large enough to hold all of the 
        # risk series data so we need to make it bigger if necessary
        longest_risk_series = max(self.risk_series.size, self.CT_risk_series.size,
                                    self.HI_risk_series.size, self.LR_risk_series.size, core_temperature_series.size)

        num_to_append = longest_risk_series - df.shape[0]
        if num_to_append > 0:
            # Add a bunch of empty (NAN) values to the dataframe is we need extra space
            # for the risk vlaues
            filler = np.empty()
            filler[:] = np.NAN
            df.append(filler)

        # Add the risk/Estimated Core temperature data to the DataFrame
        df.loc[range(self.risk_series.size), "time Risk"] = self.risk_series.keys()
        df.loc[range(self.risk_series.size), "Risk"] = self.risk_series.values

        df.loc[range(self.HI_risk_series.size), "time HI Risk"] = self.HI_risk_series.keys()
        df.loc[range(self.HI_risk_series.size), "HI Risk"] = self.HI_risk_series.values

        df.loc[range(self.CT_risk_series.size), "time CT Risk"] = self.CT_risk_series.keys()
        df.loc[range(self.CT_risk_series.size), "CT Risk"] = self.CT_risk_series.values

        df.loc[range(self.LR_risk_series.size), "time LR Risk"] = self.LR_risk_series.keys()
        df.loc[range(self.LR_risk_series.size), "LR Risk"] = self.LR_risk_series.values

        df.loc[range(core_temperature_series.size), "time est CT"] = core_temperature_series.keys()
        df.loc[range(core_temperature_series.size), "est CT"] = core_temperature_series.values

        # Save the data frame to file! yaas!
        df.to_csv(self.data_save_file)

def progress_bar(progress, filler="="):
    # This function makes a string that looks like a progress bar
    # Example: progress of 0.62 would give the following string: "[======    ]""
    return "[" + filler * int(0.5 + progress / 0.1) + " " * (1 + int(0.5 + (1 - progress) / 0.1)) + "]"

def test(args):
    logger.debug("Instantiating prediciton handler...")
    handler = PredictionHandler(users_XML= args.users_XML, username=args.user, output_dir=args.output)
    logger.debug(emoji.emojize("Prediction handler instantiated :heavy_check_mark:"))

    # Tell the prediction handler whether or not to use prefiltered data or to refilter it
    handler.predictor.use_prefiltered = args.prefiltered
    # Initialize the logistic regression predictor using the filtered data
    handler.predictor.init_log_reg_predictor()

    # Create all of the threads that the handler needs
    handler.initialize_threads(test=args.no_bean)

    # Start all of the threads
    logger.info("Starting data collection thread...")
    handler.start_data_collection()
    logger.info("Starting data saving thread...")
    handler.saving_thread.start()
    logger.info("Starting prediction thread...")
    handler.start_prediction_thread()

    try:
        logger.warning("Pausing main thread ('q' or control-C to abort)...")
        # This makes is so that the user can press any key on the keyboard
        # but it won't exit unless they KeyboardInterrupt the process
        while True:
            user_input = input("")
            if user_input == 'q':
                logger.warning("Exit signal recieved. Terminating threads...")
            break
    except KeyboardInterrupt:
        logger.warning("Keyboard Interrupt. Terminating threads...")

    # Save the data
    handler.save_all_data()
    # Stop the threads
    handler.stop_all_threads(wait=True)
    # Indicate that the test has finished
    logger.info(emoji.emojize("Test complete. :heavy_check_mark:"))

def main():
    import argparse
    script_description = "This script reads data from a monitor and uses"
    parser = argparse.ArgumentParser(description=script_description)

    input_group = parser.add_argument_group("Inputs")
    input_group.add_argument('-in', '--input', required=False, help='Input spreadsheet with case data')

    output_group = parser.add_argument_group("Outputs")
    output_group.add_argument("-out", "--output", help="Output directory")

    options_group = parser.add_argument_group("Opitons")
    options_group.add_argument("-f", "--fake", action="store_true", help="Use fake data")
    options_group.add_argument('-p', '--prefiltered', action="store_true", help="Use pre-filtered data")
    options_group.add_argument('-all', "--all-fields", dest="all_fields", action="store_true", help="Use all fields")
    options_group.add_argument('-test', '--test', action="store_true", help="Implementation testing")
    options_group.add_argument('-u', '--user', default=None, help="Monitor user name")
    options_group.add_argument("--users", dest="users_XML", default=None, help="Monitor users XML file")
    options_group.add_argument('-nb', '--no-bean', dest="no_bean", action="store_true", help="Don't read from serial port")

    console_options_group = parser.add_argument_group("Console Options")
    console_options_group.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    console_options_group.add_argument('--debug', action='store_true', help='Debug console')

    args = parser.parse_args()

    if args.debug:
        coloredlogs.install(level='DEBUG')
    elif args.verbose:
        warnings.filterwarnings('ignore')
        coloredlogs.install(level='INFO')
    else:
        warnings.filterwarnings('ignore')
        coloredlogs.install(level='WARNING')

    if args.test:
        logger.info(emoji.emojize('Initializing test...' + ' :fire:' * 3))
        test(args)
    else:
        logger.warning("Integrated prediction not yet implemented. Use the --test flag.")

if __name__ == "__main__":
    main()