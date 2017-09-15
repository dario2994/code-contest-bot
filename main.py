#!/usr/bin/env python3

import os
import time
import datetime
import pickle
from config import bot_secret_token, admin_password, data_dump_file
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


def timestamp2time(ts):
    return datetime.datetime.fromtimestamp(ts).strftime('%H:%M:%S')


class CompleteState:
    def __init__(self):
        self.admins = []
        self.contestants = []
        self.problems = []
        self.current_problem = None
        self.scores = {}  # (contestant, problem): score

    def is_admin(self, name):
        for admin in self.admins:
            if admin.name == name:
                return True
        return False

    def is_contestant(self, name):
        for contestant in self.contestants:
            if contestant.name == name:
                return True
        return False


class CodeContestError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return repr(self.msg)


class User:
    def __init__(self, name, chat_id):
        self.chat_id = chat_id
        self.name = name


class Problem:
    def __init__(self, name, t1, t2, url):
        assert(t1 > 0 and t2 > 0)
        self.name = name
        self.t1 = t1
        self.t2 = t2
        self.url = url
        self.starting_time = time.time()

    def give_score(self):
        current_time = time.time()
        delta_min = (current_time - self.starting_time) / 60
        if delta_min <= self.t1:
            return 100
        if delta_min > self.t2:
            return 0
        score = (self.t2 - delta_min) / (self.t2 - self.t1)
        return int(score * 100)


def save_data_on_disk():
    with open(data_dump_file, 'wb') as handle:
            pickle.dump(data, handle, protocol=pickle.HIGHEST_PROTOCOL)


def load_data_from_disk():
    if os.path.isfile(data_dump_file):
        with open(data_dump_file, 'rb') as handle:
            return pickle.load(handle)
    return CompleteState()


# BEGIN COMMAND HANDLERS


class CommandDescription:
    def __init__(self, usage, admin_only=False, contestant_only=False):
        self.usage = usage
        self.admin_only = admin_only
        self.contestant_only = contestant_only


COMMAND_DESCRIPTIONS = {
    'Register as contestant': CommandDescription('/i_am_contestant'),
    'Register as admin': CommandDescription('/i_am_admin <password>'),
    'Create problem': CommandDescription('/create_problem <problem name> <T1> <T2> <problem url>', admin_only=True),
    'Add submission': CommandDescription('Send to the bot a screenshot of the \'ACCEPTED\' page.', contestant_only=True),
    'Delete submission': CommandDescription('/delete_submission <contestant surname> <problem name>', admin_only=True),
    'See ranking': CommandDescription('/ranking'),
    'Get help': CommandDescription('/help'),
}


def i_am_contestant(bot, update):
    name = update.effective_user.last_name
    chat_id = update.message.chat_id
    try:
        if data.is_contestant(name):
            raise CodeContestError('You are already registered as contestant.')
        data.contestants.append(User(name, chat_id))
        save_data_on_disk()

        bot.send_message(chat_id=chat_id, text='You are now registered as contestant.')
    except CodeContestError as error:
        bot.send_message(chat_id=chat_id, text=error.msg)


def i_am_admin(bot, update, args):
    name = update.effective_user.last_name
    chat_id = update.message.chat_id
    try:
        if len(args) != 1:
            raise CodeContestError('Usage: {0} .'.format(COMMAND_DESCRIPTIONS['Register as admin'].usage))
        if args[0] != admin_password:
            raise CodeContestError('Wrong password')
        if data.is_admin(name):
            raise CodeContestError('You are already registered as admin.')
        data.admins.append(User(name, chat_id))
        save_data_on_disk()

        bot.send_message(chat_id=chat_id, text='You are now registered as admin.')
    except CodeContestError as error:
        bot.send_message(chat_id=chat_id, text=error.msg)


def create_problem(bot, update, args):
    name = update.effective_user.last_name
    chat_id = update.message.chat_id
    try:
        if not data.is_admin(name):
            raise CodeContestError('Only admins can create a problem.')
        if len(args) != 4:
            raise CodeContestError('Usage: {0} .'.format(COMMAND_DESCRIPTIONS['Create problem'].usage))
        try:
            data.current_problem = Problem(args[0], int(args[1]), int(args[2]), args[3])
        except ValueError:
            raise CodeContestError('Usage: {0} .'.format(COMMAND_DESCRIPTIONS['Create problem'].usage))
        data.problems.append(data.current_problem)
        save_data_on_disk()

        for contestant in data.contestants:
            bot.send_message(chat_id=contestant.chat_id, text='''
New problem: {0}.
Url: {1}.
Starting time: {2} (now).
Full score until: {3} ({4} minutes).
Partial score until: {5} ({6} minutes).
            '''.format(data.current_problem.name,
                       data.current_problem.url,
                       timestamp2time(data.current_problem.starting_time),
                       timestamp2time(data.current_problem.starting_time + data.current_problem.t1 * 60),
                       data.current_problem.t1,
                       timestamp2time(data.current_problem.starting_time + data.current_problem.t2 * 60),
                       data.current_problem.t2))

        for admin in data.admins:
            bot.send_message(chat_id=admin.chat_id,
                             text='Problem \'{0}\' created and sent to all contestants.'
                                .format(data.current_problem.name))
    except CodeContestError as error:
        bot.send_message(chat_id=chat_id, text=error.msg)


def add_submission(bot, update):
    name = update.effective_user.last_name
    chat_id = update.message.chat_id
    try:
        if data.current_problem is None:
            raise CodeContestError('There is no problem active now.')
        if not data.is_contestant(name):
            raise CodeContestError('Only a contestant can register his submissions.')
        overwrite_previous_submission = False
        if (name, data.current_problem.name) in data.scores \
                and not overwrite_previous_submission:
            raise CodeContestError('You have already registered a submission for the current problem.')

        score = data.current_problem.give_score()
        data.scores[(name, data.current_problem.name)] = score
        save_data_on_disk()

        for admin in data.admins:
            bot.send_photo(chat_id=admin.chat_id, photo=update.message.photo[0],
                           caption="New submission. Contestant: {0}, Problem: {1}".format(name, data.current_problem.name))
        bot.send_message(chat_id=chat_id, text='Your submission was awarded a score of: {0}'.format(score))
    except CodeContestError as error:
        bot.send_message(chat_id=chat_id, text=error.msg)


def delete_submission(bot, update, args):
    name = update.effective_user.last_name
    chat_id = update.message.chat_id
    try:
        if not data.is_admin(name):
            raise CodeContestError('Only admins can delete a submission.')
        if len(args) != 2:
            raise CodeContestError('Usage: {0} .'.format(COMMAND_DESCRIPTIONS['Delete submission'].usage))
        contestant_name = args[0]
        problem_name = args[1]
        if (contestant_name, problem_name) not in data.scores:
            raise CodeContestError('The submission ({0}, {1}) does not exist.'.format(contestant_name, problem_name))
        del data.scores[(contestant_name, problem_name)]
        for admin in data.admins:
            bot.send_message(chat_id=admin.chat_id, text='The submission of {0} on problem \'{1}\' has been deleted.'.format(contestant_name, problem_name))
        for contestant in data.contestants:
            if contestant.name != contestant_name:
                continue
            bot.send_message(chat_id=contestant.chat_id, text='Your submission on problem \'{0}\' has been deleted by an admin. You can submit again.'.format(problem_name))
    except CodeContestError as error:
        bot.send_message(chat_id=chat_id, text=error.msg)


def ranking(bot, update):
    chat_id = update.message.chat_id

    row_format = "{:<20}" + "{:<10}" * (len(data.problems) + 1)

    header = row_format.format('Contestant', *[problem.name for problem in data.problems], 'Total')
    rows = []
    for contestant in data.contestants:
        total = 0
        individual_scores = []
        for problem in data.problems:
            if (contestant.name, problem.name) in data.scores:
                score = data.scores[(contestant.name, problem.name)]
                individual_scores.append(score)
                total += score
            else:
                individual_scores.append('-')
        rows.append(row_format.format(contestant.name, *individual_scores, total))
    msg = header + '\n' + '\n'.join(rows)
    bot.send_message(chat_id=chat_id, text='```txt\n' + msg + ' ```',
                     parse_mode=telegram.ParseMode.MARKDOWN)


def help(bot, update):
    chat_id = update.message.chat_id

    header = '*CodeContest* helps you managing a contest of competitive programming.'
    rows = []
    for command in COMMAND_DESCRIPTIONS:
        row = '*' + command + '*'
        if COMMAND_DESCRIPTIONS[command].admin_only:
            row += ' (admins only)'
        if COMMAND_DESCRIPTIONS[command].contestant_only:
            row += ' (contestants only)'
        row += '\n'
        row += COMMAND_DESCRIPTIONS[command].usage
        rows.append(row)
    msg = header + '\n\n' + '\n\n'.join(rows)
    msg = msg.replace('_', '\_')
    bot.send_message(chat_id=chat_id, text=msg,
                     parse_mode=telegram.ParseMode.MARKDOWN)


# END COMMAND HANDLERS


def start_bot():
    updater = Updater(token=bot_secret_token)
    dispatcher = updater.dispatcher

    i_am_contestant_handler = CommandHandler('i_am_contestant', i_am_contestant)
    dispatcher.add_handler(i_am_contestant_handler)

    i_am_admin_handler = CommandHandler(
        'i_am_admin', i_am_admin, pass_args=True)
    dispatcher.add_handler(i_am_admin_handler)

    create_problem_handler = CommandHandler(
        'create_problem', create_problem, pass_args=True)
    dispatcher.add_handler(create_problem_handler)

    add_submission_handler = MessageHandler(Filters.photo, add_submission)
    dispatcher.add_handler(add_submission_handler)

    delete_submission_handler = CommandHandler(
        'delete_submission', delete_submission, pass_args=True)
    dispatcher.add_handler(delete_submission_handler)

    ranking_handler = CommandHandler('ranking', ranking)
    dispatcher.add_handler(ranking_handler)

    help_handler = CommandHandler('help', help)
    dispatcher.add_handler(help_handler)

    start_handler = CommandHandler('start', help)
    dispatcher.add_handler(start_handler)

    dispatcher.add_error_handler(telegram.error)

    updater.start_polling()
    updater.idle()


data = load_data_from_disk()
start_bot()
