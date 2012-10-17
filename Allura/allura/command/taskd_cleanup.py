import os
import signal
import socket
import subprocess
from ming.orm.ormsession import ThreadLocalORMSession

from allura import model as M
import base

class TaskdCleanupCommand(base.Command):
    summary = 'Tasks cleanup command'
    parser = base.Command.standard_parser(verbose=True)
    parser.add_option('-k', '--kill-stuck-taskd',
            dest='kill', action='store_true',
            help='automatically kill stuck taskd processes')
    usage = '<ini file> [-k] <taskd status log file>'
    min_args = 2
    max_args = 2

    def command(self):
        self.basic_setup()
        self.hostname = socket.gethostname()
        self.taskd_status_log = self.args[1]
        self.stuck_pids = []
        self.error_tasks = []
        self.suspicious_tasks = []

        taskd_pids = self._taskd_pids()
        base.log.info('Taskd processes on %s: %s' % (self.hostname, taskd_pids))

        # find stuck taskd processes
        base.log.info('Seeking for stuck taskd processes')
        for pid in taskd_pids:
            base.log.info('...sending USR1 to %s and watching status log' % (pid))
            status = self._check_taskd_status(int(pid))
            if status != 'OK':
                base.log.info('...taskd pid %s has stuck' % pid)
                self.stuck_pids.append(pid)
                if self.options.kill:
                    base.log.info('...-k is set. Killing %s' % pid)
                    self._kill_stuck_taskd(pid)
            else:
                base.log.info('...%s' % status)

        # find 'forsaken' tasks
        base.log.info('Seeking for forsaken busy tasks')
        tasks = [t for t in self._busy_tasks()
                 if t not in self.error_tasks]  # skip seen tasks
        base.log.info('Found %s busy tasks on %s' % (len(tasks), self.hostname))
        for task in tasks:
            base.log.info('Verifying task %s' % task)
            pid = task.process.split()[-1]
            if pid not in taskd_pids:
                # 'forsaken' task
                base.log.info('Task is forsaken '
                    '(can\'t find taskd with given pid). '
                    'Setting state to \'error\'')
                task.state = 'error'
                task.result = 'Can\'t find taskd with given pid'
                self.error_tasks.append(task)
            else:
                # check if taskd with given pid really processing this task now:
                base.log.info('Checking that taskd pid %s is really processing task %s' % (pid, task._id))
                status = self._check_task(pid, task)
                if status != 'OK':
                    # maybe task moved quickly and now is complete
                    # so we need to check such tasks later
                    # and mark incomplete ones as 'error'
                    self.suspicious_tasks.append(task)
                    base.log.info('...NO. Adding task to suspisious list')
                else:
                    base.log.info('...OK')

        # check suspicious task and mark incomplete as error
        base.log.info('Checking suspicious list for incomplete tasks')
        self._check_suspicious_tasks()
        ThreadLocalORMSession.flush_all()
        self.print_summary()

    def print_summary(self):
        base.log.info('-' * 80)
        if self.stuck_pids:
            base.log.info('Found stuck taskd: %s' % self.stuck_pids)
            if self.options.kill:
                base.log.info('...stuck taskd processes were killed')
            else:
                base.log.info('...to kill these processes run command with -k flag')
        if self.error_tasks:
            base.log.info('Tasks marked as \'error\': %s' % self.error_tasks)

    def _busy_tasks(self, pid=None):
        regex = '^%s ' % self.hostname
        if pid is not None:
            regex = '^%s pid %s' % (self.hostname, pid)
        return M.MonQTask.query.find({
            'state': 'busy',
            'process': {'$regex': regex}
        })

    def _taskd_pids(self):
        p = subprocess.Popen(['pgrep', '-f', '/paster taskd'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()
        tasks = []
        if p.returncode == 0:
            # p.communicate() returns self-process too,
            # so we need to skip last pid
            tasks = [pid for pid in stdout.split('\n') if pid != ''][:-1]
        return tasks

    def _taskd_status(self, pid):
        os.kill(int(pid), signal.SIGUSR1)
        p = subprocess.Popen(['tail', '-n1', self.taskd_status_log],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()
        if p.returncode != 0:
            base.log.error('Can\'t read taskd status log %s' % self.taskd_status_log)
            exit(1)
        return stdout

    def _check_taskd_status(self, pid):
        status = self._taskd_status(pid)
        if ('taskd pid %s' % pid) not in status:
            return 'STUCK'
        return 'OK'

    def _check_task(self, taskd_pid, task):
        status = self._taskd_status(taskd_pid)
        line = 'taskd pid %s is currently handling task %s' % (taskd_pid, task)
        if line not in status:
            return 'FAIL'
        return 'OK'

    def _kill_stuck_taskd(self, pid):
        os.kill(int(pid), signal.SIGKILL)
        # find all 'busy' tasks for this pid and mark them as 'error'
        tasks = list(self._busy_tasks(pid=pid))
        base.log.info('...taskd pid %s has assigned tasks: %s. '
                'setting state to \'error\' for all of them' % (pid, tasks))
        for task in tasks:
            task.state = 'error'
            task.result = 'Taskd has stuck with this task'
            self.error_tasks.append(task)

    def _complete_suspicious_tasks(self):
        complete_tasks = M.MonQTask.query.find({
            'state': 'complete',
            '_id': {'$in': [t._id for t in self.suspicious_tasks]}
        });
        return [t._id for t in complete_tasks]

    def _check_suspicious_tasks(self):
        if not self.suspicious_tasks:
            return
        complete_tasks = self._complete_suspicious_tasks()
        for task in self.suspicious_tasks:
            base.log.info('Verifying task %s' % task)
            if task._id not in complete_tasks:
                base.log.info('...incomplete. Setting status to \'error\'')
                task.state = 'error'
                task.result = 'Forsaken task'
                self.error_tasks.append(task)
            else:
                base.log.info('...complete')