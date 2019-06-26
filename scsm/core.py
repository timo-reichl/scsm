import os
import platform as pf
import re
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile
from urllib.request import urlretrieve

import vdf
from ruamel.yaml import YAML

from .config import Config


class App():
    def __init__(self, app, app_dir, backup_dir=None, platform=None):
        self.app_id, self.app_name, self.server_name = Index.search(app)

        # app_id is required, if None raise FileNotFoundError
        f = f'{self.app_id}.yaml'
        self.config_f = Path(Config.data_dir, 'apps', f)
        self.config_is_default = True

        # check for user app config file
        d = Path(Config.config_dir, 'apps')
        if d.exists() and Path(d, f).is_file():
            self.config_f = Path(d, f)
            self.config_is_default = False

        with open(self.config_f, 'r') as f:
            yaml = YAML(typ='safe')
            data = yaml.load(f)

        self.app_names = list(data['apps'].keys())
        if not self.app_name:
            self.app_name = self.app_names[0]

        self.server_names = list(data['apps'][self.app_name]['servers'].keys())

        data = data['apps'][self.app_name]
        self.full_name = data['fname']

        if self.server_name:
            self.start_options = data['servers'][self.server_name]['start']
            self.stop_options = data['servers'][self.server_name]['stop']

        self.app_dir = Path(app_dir, str(self.app_id), self.app_name)
        if backup_dir:
            self.backup_dir = Path(backup_dir, str(self.app_id), self.app_name)

        self.beta, self.beta_password, self.app_config = None, None, None
        try:
            self.beta = data['beta']
            self.beta_password = data['password']
            self.app_config = data['app_config']
        except KeyError:
            pass

        if not platform:
            self.platform = pf.system()
        else:
            self.platform = platform

        self.platforms = data['platforms'].keys()
        self.arch = pf.architecture()[0]

        if self.platform in self.platforms:
            if 'exec' in data['platforms'][self.platform].keys():
                data = data['platforms'][self.platform]
            else:
                data = data['platforms'][self.platform][self.arch]

            self.exe = data['exec']

            if 'directory' in data.keys():
                self.exec_dir = Path(self.app_dir, data['directory'])
            else:
                self.exec_dir = self.app_dir

    @property
    def build_id_local(self):
        '''Return the app's local build id'''
        f = Path(self.app_dir, 'steamapps', f'appmanifest_{self.app_id}.acf')

        if f.is_file():
            with open(f, 'r') as app_manifest:
                data = vdf.load(app_manifest)
            return int(data['AppState']['buildid'])
        return 0

    @property
    def build_id_steam(self):
        '''Return the app's steam build id'''
        steamcmd = SteamCMD()
        data = steamcmd.info(self.app_id)

        if data:
            if self.beta:
                return int(data['depots']['branches'][self.beta]['buildid'])
            return int(data['depots']['branches']['public']['buildid'])
        return 0

    @property
    def installed(self):
        '''Return True if app is installed'''
        if self.app_dir.exists():
            for d in self.app_dir.iterdir():
                # if only steamapps directory is found
                # then it did not completely install
                if d != 'steamapps':
                    return True
        return False

    @property
    def running(self):
        '''Return True if app is running'''
        # Server functions do not work on Windows
        if self.platform != 'Windows':
            return Server.running_check(self.app_name)
        return False

    def backup(self, compression=None):
        '''Backup app to backup_dir using tar'''
        if not compression:
            compression = ''
            extension = '.tar'
        else:
            extension = f'.tar.{compression}'

        date = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        f = Path(self.backup_dir, f'{date}{extension}')

        with tarfile.open(f, f'w:{compression}') as tar:
            os.chdir(self.app_dir.parent)
            tar.add(self.app_name)

    def copy_config(self):
        '''Copy default app config file to config_dir'''
        f = f'{self.app_id}.yaml'
        shutil.copyfile(Path(Config.data_dir, 'apps', f),
                        Path(Config.config_dir, 'apps', f))
        self.config_is_default = False

    def remove(self):
        '''Remove app directory'''
        shutil.rmtree(self.app_dir)

        # if this app is the only one installed for that app_id
        # remove the now empty app_id directory as well
        app_dir = self.app_dir.parent
        if not os.listdir(app_dir):
            app_dir.rmdir()

    def restore(self, backup):
        '''Restore specified backup file'''
        with tarfile.open(Path(self.backup_dir, backup)) as tar:
            tar.extractall(self.app_dir.parent)

        if self.config_is_default:
            self.copy_config()

    def update(self, username='anonymous', password='',
               steam_guard='', validate=False, verbose=False):
        '''Update app using steamcmd'''
        new_install = not self.app_dir.exists()

        if self.config_is_default:
            self.copy_config()

        steamcmd = SteamCMD()
        exit_code, text = steamcmd.app_update(self.app_id, self.app_dir,
                                              self.beta, self.beta_password,
                                              self.app_config, self.platform,
                                              validate, username, password,
                                              steam_guard, verbose)

        # remove partially downloaded files from no subscription error
        if '(No subscription)' in text and new_install:
            self.remove()

        return exit_code, text


class Index():
    '''Used for working with app_index.yaml'''
    f = Path(Config.config_dir, 'app_index.yaml')

    @staticmethod
    def config_dirs():
        '''Get app config dirs'''
        for d in Config.data_dir, Config.config_dir:
            d = Path(d, 'apps')
            if d.exists():
                yield d

    @staticmethod
    def list(directory):
        '''Return appid or app_name if not only app for app_id'''
        directory = Path(directory)
        if not directory.exists():
            return

        with open(Index.f, 'r') as f:
            yaml = YAML(typ='safe')
            data = yaml.load(f)

        for app_id in directory.iterdir():
            if len(data[int(app_id.name)].keys()) > 1:
                for app_name in Path(directory, app_id).iterdir():
                    yield app_name.name
            else:
                yield app_id.name

    @staticmethod
    def list_all():
        '''Return generator of all app_id's in index'''
        with open(Index.f, 'r') as f:
            yaml = YAML(typ='safe')
            data = yaml.load(f)

        for app_id in data.keys():
            app_names = data[app_id].keys()

            if len(app_names) > 1:
                for app_name in app_names:
                    yield app_name
            else:
                yield app_id

    @staticmethod
    def search(app):
        '''Search index for app and return app_id, app_name, and server_name'''
        try:
            app = int(app)
        except ValueError:
            pass

        with open(Index.f, 'r') as f:
            yaml = YAML(typ='safe')
            data = yaml.load(f)

        if app in data.keys():
            return app, None, None

        for app_id in data.keys():
            if app in data[app_id].keys():
                if app in data[app_id][app]:
                    return app_id, app, app
                return app_id, app, None
            for app_name in data[app_id].keys():
                if app in data[app_id][app_name]:
                    return app_id, app_name, app
        return None, None, None

    @staticmethod
    def update():
        '''Update index with latst app config files'''
        app_index = {}
        for d in Index.config_dirs():
            for f in d.iterdir():
                if Path(f).suffix == '.yaml':
                    with open(Path(d, f), 'r') as config_f:
                        yaml = YAML(typ='safe')
                        data = yaml.load(config_f)

                    for app in data['apps'].keys():
                        app_index[data['app_id']] = {app: list(data['apps'][app]
                                                               ['servers'].keys())}

        with open(Index.f, 'w') as f:
            yaml = YAML(typ='safe')
            yaml.dump(app_index, f)


class Server(App):
    def __init__(self, app, app_dir, backup_dir=None,  platform=None):
        super(Server, self).__init__(app, app_dir, backup_dir, platform)
        if not self.server_name:
            self.server_name = self.server_names[0]
        self.session = f'{self.app_name}-{self.server_name}'

    @property
    def running(self):
        '''Return True if app is running'''
        return Server.running_check(self.app_name, self.server_name)

    def console(self):
        '''Attach to screen session'''
        cmd = ['screen', '-r', self.session]
        return subprocess.run(cmd, shell=False).returncode

    def kill(self):
        '''Kill screen session'''
        cmd = ['screen', '-S', self.session, '-X', 'quit']
        subprocess.run(cmd, shell=False)

    @staticmethod
    def running_check(app_name, server_name=None):
        '''Check if server or app is running'''
        if server_name:
            session = f'{app_name}-{server_name}'
        else:
            session = f'{app_name}-.*'

        proc = subprocess.run(['screen', '-ls'], stdout=subprocess.PIPE, shell=False)

        for line in proc.stdout.decode().split('\n'):
            if re.search(fr'{session}\s', line):
                return True
        return False

    def send(self, command):
        '''Send command to screen session'''
        cmd = ['screen', '-S', self.session, '-X', 'stuff', f'{command}\n']
        return subprocess.run(cmd, shell=False).returncode

    def start(self, debug=False):
        '''Start server'''
        if debug:
            cmd = []
        else:
            cmd = ['screen', '-dmS', self.session]

        cmd.extend(self.exe.split(' '))

        if self.start_options:
            if self.start_options[0].endswith('?'):
                cmd.append(''.join(self.start_options))
            else:
                cmd.extend(self.start_options)

        os.chdir(self.exec_dir)
        subprocess.run(cmd, shell=False)

    def stop(self):
        '''Stop server'''
        if self.stop_options:
            for command in self.stop_options:
                self.send(command)
        else:
            # Send Ctrl - C to screen session
            self.send('$\'\003\'')


class SteamCMD():
    def __init__(self):
        if shutil.which('steamcmd'):
            self.exe = 'steamcmd'
        else:
            if pf.system() != 'Windows':
                self.directory = Path('~/.local/share/scsm/steamcmd').expanduser()
                self.exe = Path(self.directory, 'steamcmd.sh')
            else:
                self.directory = Path(os.getenv('APPDATA'), 'scsm', 'steamcmd')
                self.exe = Path(self.directory, 'self.exe')

    @property
    def installed(self):
        '''Return True if installed'''
        if self.exe == 'steamcmd':
            return True
        return self.directory.exists() and self.exe.is_file()

    def app_update(self, app_id, app_dir, beta=None, beta_password=None,
                   config=None, platform=None, validate=False,
                   username='anonymous', password='', steam_guard='',
                   verbose=False):
        '''+app_update wrapper'''
        cmd = ['+login', username, password, steam_guard, '+force_install_dir',
               app_dir, '+app_update', str(app_id), '+quit']

        if config:
            cmd.insert(-3, f'+app_set_config {app_id} {config}')
        if beta:
            cmd.insert(-1, f'-beta {beta}')
        if beta_password:
            cmd.insert(-1, f'-betapassword {beta_password}')
        if validate:
            cmd.insert(-1, 'validate')

        if platform and platform != pf.system():
            if platform == 'Darwin':
                platform = 'macos'
            elif platform == 'Linux':
                platform = 'linux'
            elif platform == 'Windows':
                platform = 'windows'

            cmd.insert(1, f'+@sSteamCmdForcePlatformType {platform}')

        return self.run(cmd, verbose=verbose)

    def cached_login(self, username):
        '''Check if user has a cached login'''
        cmd = [self.exe, '+login', username, '+quit']
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                              timeout=5, shell=False)

        for line in proc.stdout.decode().split('\n'):
            if 'Using cached credentials' in line:
                return True
        return False

    def filter(self, cmd):
        '''Run command and filter the output'''
        success = ['Success', 'Update complete']
        error = ['ERROR', 'Failed', 'Fatal Error']

        proc = subprocess.run(cmd, stdout=subprocess.PIPE, shell=False)

        for line in reversed(proc.stdout.decode().split('\n')):
            if any(word in line for word in success + error):
                return proc.returncode, line
        return proc.returncode, None

    def info(self, app_id):
        ''''Return app info as dict'''
        cmd = [self.exe, '+login', 'anonymous', '+app_info_update', '1',
               '+app_info_print', str(app_id), '+quit']

        out = subprocess.run(cmd, stdout=subprocess.PIPE, shell=False).stdout.decode().split('\n')
        start, end = 0, 0

        # find the start and end of the vdf file from output
        for i, line in enumerate(out):
            if not start and f'"{app_id}"' == line:
                start = i
                continue
            elif line == '}':
                end = i
                break

        data = '\n'.join(out[start:end + 1])
        return vdf.loads(data)[str(app_id)]

    def install(self):
        '''Install steamcmd'''
        if pf.system() == 'Darwin':
            f = 'steamcmd_osx.tar.gz'
        elif pf.system() == 'Linux':
            f = 'steamcmd_linux.tar.gz'
        elif pf.system() == 'Windows':
            f = 'steamcmd.zip'

        base_url = 'https://steamcdn-a.akamaihd.net/client/installer'
        url = f'{base_url}/{f}'

        self.directory.mkdir(parents=True, exist_ok=True)
        urlretrieve(url, Path(self.directory, f))

        if pf.system() != 'Windows':
            with tarfile.open(Path(self.directory, f)) as tar:
                tar.extractall(self.directory)
        else:
            with ZipFile(Path(self.directory, f)) as zipf:
                zipf.extractall(self.directory)

    def license(self, app_id, username='anonymous', password='', steam_guard=''):
        '''Check if user has a license for app_id'''
        cmd = [self.exe, '+login', username, password, steam_guard,
               '+licenses_for_app', str(app_id), '+quit']

        out = subprocess.run(cmd, stdout=subprocess.PIPE, shell=False).stdout.decode()

        for line in out.split('\n'):
            if 'License packageID' in line:
                return True
        return False

    def remove(self):
        '''Remove steamcmd'''
        shutil.rmtree(self.directory)

    def run(self, args, username='anonymous', password='', steamguard='', verbose=False):
        '''Run steamcmd with args and login'''
        args = [self.exe, username, password, steamguard] + args

        if verbose:
            return subprocess.run(args, shell=False).returncode, None
        return self.filter(args)

    def update(self, verbose=False):
        '''Update steamcmd'''
        return self.run(['+quit'], verbose=verbose)
