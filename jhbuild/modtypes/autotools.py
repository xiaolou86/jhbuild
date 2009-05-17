# jhbuild - a build script for GNOME 1.x and 2.x
# Copyright (C) 2001-2006  James Henstridge
# Copyright (C) 2007-2008  Frederic Peters
#
#   autotools.py: autotools module type definitions.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

__metaclass__ = type

import os
import re
import stat

from jhbuild.errors import FatalError, BuildStateError, CommandError
from jhbuild.modtypes import \
     Package, get_dependencies, get_branch, register_module_type, SkipToEnd
from jhbuild.modtypes.debian import DebianBasePackage

from jhbuild.utils.cache import get_cached_value, write_cached_value

__all__ = [ 'AutogenModule' ]

class AutogenModule(Package, DebianBasePackage):
    '''Base type for modules that are distributed with a Gnome style
    "autogen.sh" script and the GNU build tools.  Subclasses are
    responsible for downloading/updating the working copy.'''
    type = 'autogen'

    PHASE_CHECKOUT       = 'checkout'
    PHASE_FORCE_CHECKOUT = 'force_checkout'
    PHASE_CLEAN          = 'clean'
    PHASE_DISTCLEAN      = 'distclean'
    PHASE_CONFIGURE      = 'configure'
    PHASE_BUILD          = 'build'
    PHASE_CHECK          = 'check'
    PHASE_DIST           = 'dist'
    PHASE_INSTALL        = 'install'

    def __init__(self, name, branch, autogenargs='', makeargs='',
                 makeinstallargs='',
                 dependencies=[], after=[], suggests=[],
                 supports_non_srcdir_builds=True,
                 skip_autogen=False,
                 autogen_sh='autogen.sh',
                 makefile='Makefile',
                 autogen_template=None,
                 check_target=True):
        Package.__init__(self, name, dependencies, after, suggests)
        self.branch = branch
        self.autogenargs = autogenargs
        self.makeargs    = makeargs
        self.makeinstallargs = makeinstallargs
        self.supports_non_srcdir_builds = supports_non_srcdir_builds
        self.skip_autogen = skip_autogen
        self.autogen_sh = autogen_sh
        self.makefile = makefile
        self.autogen_template = autogen_template
        self.check_target = check_target

    def get_srcdir(self, buildscript):
        return self.branch.srcdir

    def get_builddir(self, buildscript):
        if buildscript.config.buildroot and self.supports_non_srcdir_builds:
            d = buildscript.config.builddir_pattern % (
                os.path.basename(self.get_srcdir(buildscript)))
            return os.path.join(buildscript.config.buildroot, d)
        else:
            return self.get_srcdir(buildscript)

    def get_revision(self):
        return self.branch.tree_id()

    def do_checkout(self, buildscript):
        self.checkout(buildscript)
    do_checkout.error_phases = [PHASE_FORCE_CHECKOUT]
    do_deb_checkout = do_checkout

    def skip_force_checkout(self, buildscript, last_state):
        return False

    def do_force_checkout(self, buildscript):
        buildscript.set_action(_('Checking out'), self)
        self.branch.force_checkout(buildscript)
    do_force_checkout.error_phases = [PHASE_FORCE_CHECKOUT]

    def skip_configure(self, buildscript, last_phase):
        # skip if manually instructed to do so
        if self.skip_autogen is True:
            return True

        # don't skip this stage if we got here from one of the
        # following phases:
        if last_phase in [self.PHASE_FORCE_CHECKOUT,
                          self.PHASE_CLEAN,
                          self.PHASE_BUILD,
                          self.PHASE_INSTALL]:
            return False

        if self.skip_autogen == 'never':
            return False

        # skip if the makefile exists and we don't have the
        # alwaysautogen flag turned on:
        builddir = self.get_builddir(buildscript)
        return (os.path.exists(os.path.join(builddir, self.makefile)) and
                not buildscript.config.alwaysautogen)
    skip_deb_configure = skip_configure

    def do_configure(self, buildscript):
        builddir = self.get_builddir(buildscript)
        if buildscript.config.buildroot and not os.path.exists(builddir):
            os.makedirs(builddir)
        buildscript.set_action(_('Configuring'), self)

        srcdir = self.get_srcdir(buildscript)
        if self.autogen_sh == 'autogen.sh':
            # if there is no autogen.sh, automatically fallback to configure
            if not os.path.exists(os.path.join(srcdir, 'autogen.sh')) and \
                    os.path.exists(os.path.join(srcdir, 'configure')):
                self.autogen_sh = 'configure'

        try:
            if not (os.stat(os.path.join(srcdir, self.autogen_sh))[stat.ST_MODE] & 0111):
                os.chmod(os.path.join(srcdir, self.autogen_sh), 0755)
        except:
            pass

        if self.autogen_template:
            template = self.autogen_template
        else:
            template = ("%(srcdir)s/%(autogen-sh)s --prefix %(prefix)s"
                        " --libdir %(libdir)s %(autogenargs)s ")

        autogenargs = self.autogenargs + ' ' + self.config.module_autogenargs.get(
                self.name, self.config.autogenargs)

        vars = {'prefix': buildscript.config.prefix,
                'autogen-sh': self.autogen_sh,
                'autogenargs': autogenargs}
                
        if buildscript.config.buildroot and self.supports_non_srcdir_builds:
            vars['srcdir'] = self.get_srcdir(buildscript)
        else:
            vars['srcdir'] = '.'

        if buildscript.config.use_lib64:
            vars['libdir'] = "'${exec_prefix}/lib64'"
        else:
            vars['libdir'] = "'${exec_prefix}/lib'"

        cmd = template % vars

        if self.autogen_sh == 'autoreconf':
            buildscript.execute(['autoreconf', '-i'], cwd = builddir,
                    extra_env = self.extra_env)
            cmd = cmd.replace('autoreconf', 'configure')
            cmd = cmd.replace('--enable-maintainer-mode', '')

        # Fix up the arguments for special cases:
        #   tarballs: remove --enable-maintainer-mode to avoid breaking build
        #   tarballs: remove '-- ' to avoid breaking build (GStreamer weirdness)
        #   non-tarballs: place --prefix and --libdir after '-- ', if present
        if self.autogen_sh == 'configure':
            cmd = cmd.replace('--enable-maintainer-mode', '')
            
            # Also, don't pass '--', which gstreamer attempts to do, since
            # it is royally broken.
            cmd = cmd.replace('-- ', '')
        else:
            # place --prefix and --libdir arguments after '-- '
            # (GStreamer weirdness)
            if autogenargs.find('-- ') != -1:
                p = re.compile('(.*)(--prefix %s )((?:--libdir %s )?)(.*)-- ' %
                       (buildscript.config.prefix, "'\${exec_prefix}/lib64'"))
                cmd = p.sub(r'\1\4-- \2\3', cmd)

        # If there is no --exec-prefix in the constructed autogen command, we
        # can safely assume it will be the same as {prefix} and substitute it
        # right now, so the printed command can be copy/pasted afterwards.
        # (GNOME #580272)
        if not '--exec-prefix' in template:
            cmd = cmd.replace('${exec_prefix}', buildscript.config.prefix)

        buildscript.execute(cmd, cwd = builddir, extra_env = self.extra_env)
    do_configure.depends = [PHASE_CHECKOUT]
    do_configure.error_phases = [PHASE_FORCE_CHECKOUT,
            PHASE_CLEAN, PHASE_DISTCLEAN]

    def skip_clean(self, buildscript, last_phase):
        srcdir = self.get_srcdir(buildscript)
        if not os.path.exists(srcdir):
            return True
        if not os.path.exists(os.path.join(srcdir, self.makefile)):
            return True
        return False
    skip_deb_clean = skip_clean

    def do_clean(self, buildscript):
        buildscript.set_action(_('Cleaning'), self)
        makeargs = self.makeargs + ' ' + self.config.module_makeargs.get(
                self.name, self.config.makeargs)
        cmd = '%s %s clean' % (os.environ.get('MAKE', 'make'), makeargs)
        buildscript.execute(cmd, cwd = self.get_builddir(buildscript),
                extra_env = self.extra_env)
    do_clean.depends = [PHASE_CONFIGURE]
    do_clean.error_phases = [PHASE_FORCE_CHECKOUT, PHASE_CONFIGURE]

    def do_build(self, buildscript):
        buildscript.set_action(_('Building'), self)
        cmd = '%s %s' % (os.environ.get('MAKE', 'make'), self.makeargs)
        buildscript.execute(cmd, cwd = self.get_builddir(buildscript),
                extra_env = self.extra_env)
    do_build.depends = [PHASE_CONFIGURE]
    do_build.error_phases = [PHASE_FORCE_CHECKOUT, PHASE_CONFIGURE,
            PHASE_CLEAN, PHASE_DISTCLEAN]

    def skip_check(self, buildscript, last_phase):
        if not self.check_target:
            return True
        if not buildscript.config.module_makecheck.get(self.name, buildscript.config.makecheck):
            return True
        return False

    def do_check(self, buildscript):
        buildscript.set_action(_('Checking'), self)
        cmd = '%s %s check' % (os.environ.get('MAKE', 'make'), self.makeargs)
        try:
            buildscript.execute(cmd, cwd = self.get_builddir(buildscript),
                    extra_env = self.extra_env)
        except CommandError:
            if not buildscript.config.makecheck_advisory:
                raise
    do_check.depends = [PHASE_BUILD]
    do_check.error_phases = [PHASE_FORCE_CHECKOUT, PHASE_CONFIGURE]

    def do_dist(self, buildscript):
        buildscript.set_action(_('Creating tarball for'), self)
        cmd = '%s %s dist' % (os.environ.get('MAKE', 'make'), self.makeargs)
        buildscript.execute(cmd, cwd = self.get_builddir(buildscript),
                    extra_env = self.extra_env)
    do_dist.depends = [PHASE_CONFIGURE]
    do_dist.error_phases = [PHASE_FORCE_CHECKOUT, PHASE_CONFIGURE]

    def do_deb_build_deps(self, buildscript):
        return DebianBasePackage.do_deb_build_deps(self, buildscript)
    do_deb_build_deps.error_phases = []

    def do_deb_build_package(self, buildscript):
        DebianBasePackage.do_deb_build_package(self, buildscript)
    do_deb_build_package.error_phases = [DebianBasePackage.PHASE_TAR_X, PHASE_DIST]

    def skip_install(self, buildscript, last_state):
        return buildscript.config.nobuild

    def do_distcheck(self, buildscript):
        buildscript.set_action(_('Creating tarball for'), self)
        cmd = '%s %s distcheck' % (os.environ.get('MAKE', 'make'), self.makeargs)
        buildscript.execute(cmd, cwd = self.get_builddir(buildscript),
                    extra_env = self.extra_env)
    do_dist.depends = [PHASE_CONFIGURE]
    do_dist.error_phases = [PHASE_FORCE_CHECKOUT, PHASE_CONFIGURE]

    def do_install(self, buildscript):
        buildscript.set_action(_('Installing'), self)
        if self.makeinstallargs:
            cmd = '%s %s' % (os.environ.get('MAKE', 'make'), self.makeinstallargs)
        else:
            cmd = '%s install' % os.environ.get('MAKE', 'make')

        buildscript.execute(cmd, cwd = self.get_builddir(buildscript),
                    extra_env = self.extra_env)
        buildscript.packagedb.add(self.name, self.get_revision() or '')
    do_install.error_phases = []
    
    def get_version(self, buildscript):
        version = get_cached_value('version-%s-%s' % (self.name, self.branch.revision_id))
        if not version:
            version = self.get_makefile_var(buildscript, 'VERSION')
            if version:
                write_cached_value('version-%s-%s' % (self.name, self.branch.revision_id), version)
        return version

    do_deb_clean = do_clean
    do_deb_build = do_build

    skip_deb_check = skip_check
    do_deb_check = do_check
    
    def do_deb_apt_get_update(self, buildscript):
        Package.do_deb_apt_get_update(self, buildscript)
    do_deb_apt_get_update.error_phases = []

    def do_deb_checkout(self, buildscript):
        return self.do_checkout(buildscript)
    do_deb_checkout.error_phases = []

    skip_deb_force_checkout = skip_force_checkout
    do_deb_force_checkout = do_force_checkout

    def get_tarball_dir(self, buildscript):
        return os.path.join(buildscript.config.tarballs_dir, self.name, self.branch.revision_id)

    do_deb_configure = do_configure

    def skip_deb_clean(self, buildscript, last_state):
        return False

    do_deb_clean = do_clean
    do_deb_build = do_build

    def skip_deb_dist(self, buildscript, last_state):
        return False

    def get_debian_version(self, buildscript):
        epoch = ''
        try:
            available_version = self.get_available_debian_version(buildscript)
            if available_version and ':' in available_version:
                epoch = available_version.split(':')[0] + ':'
        except KeyError:
            pass
        version = self.get_version(buildscript)
        return '%s%s.%s.%s-0' % (epoch, version, self.branch.repository.code, self.branch.revision_id)



    def do_deb_dist(self, buildscript):
        buildscript.set_action('Creating tarball', self)
        cmd = '%s %s dist-gzip' % (os.environ.get('MAKE', 'make'), self.makeargs)
        try:
            buildscript.execute(cmd, cwd=self.get_builddir(buildscript))
        except CommandError:
            raise BuildStateError('Failed to make dist')
        builddir = self.get_builddir(buildscript)
        version = self.get_version(buildscript)
        if not version:
            raise BuildStateError('Unable to get version number for %s' % self.name)

        for v in ('PACKAGE_TARNAME', 'PACKAGE_NAME', 'PACKAGE'):
            package_name = self.get_makefile_var(buildscript, v)
            if package_name and package_name.strip():
                break
        else:
            raise BuildStateError('failed to get package tarball name')

        tarball_filename = '%s-%s.tar.gz' % (package_name, version)
        tarball = os.path.join(builddir, tarball_filename)

        tarball_dir = self.get_tarball_dir(buildscript)
        if not os.path.exists(tarball_dir):
            os.makedirs(tarball_dir)
        buildscript.execute(['cp', tarball, tarball_dir])

        try:
            debian_version = self.get_debian_version(self, buildscript)
            available_version = self.get_available_debian_version(buildscript)
            if debian_version == available_version:
                buildscript.message('Available Debian package is already this version')
                raise SkipToEnd()
        except:
            pass
    do_deb_dist.error_phases = []

    def get_distdir(self, buildscript):
        tarball_dir = self.get_tarball_dir(buildscript)
        try:
            filename = os.listdir(tarball_dir)[0]
        except IndexError:
            raise BuildStateError('failed to get tarball')

        return filename[:-7] # removing .tar.gz

    def skip_deb_tar_x(self, buildscript, last_state):
        return False

    def do_deb_tar_x(self, buildscript):
        buildscript.set_action('Extracting tarball of', self)
        distdir = self.get_distdir(buildscript)
        v = distdir.rsplit('-')[-1]
        v = '%s.%s.%s' % (v, self.branch.repository.code, self.branch.revision_id)
        debian_name = self.get_debian_name(buildscript)
        orig_filename = '%s_%s.orig.tar.gz' % (debian_name, v)

        tarball_dir = self.get_tarball_dir(buildscript)
        builddebdir = self.get_builddebdir(buildscript)
        if not os.path.exists(builddebdir):
            os.makedirs(builddebdir)
        if os.path.exists(os.path.join(builddebdir, orig_filename)):
            os.unlink(os.path.join(builddebdir, orig_filename))
        os.symlink(os.path.join(tarball_dir, distdir + '.tar.gz'),
                os.path.join(builddebdir, orig_filename))

        if os.path.exists(os.path.join(builddebdir, distdir)):
            buildscript.execute(['rm', '-rf', distdir], cwd = builddebdir)

        buildscript.execute(['tar', 'xzf', orig_filename], cwd = builddebdir)
    do_deb_tar_x.error_phases = []

    def skip_force_clean(self, buildscript, last_state):
        return False

    def do_force_clean(self, buildscript):
        self.do_clean(buildscript)
    do_force_clean.error_phases = []

    def skip_force_distclean(self, buildscript, last_state):
        return False

    do_install.depends = [PHASE_BUILD]

    def do_distclean(self, buildscript):
        buildscript.set_action(_('Distcleaning'), self)
        cmd = '%s %s distclean' % (os.environ.get('MAKE', 'make'), self.makeargs)
        buildscript.execute(cmd, cwd = self.get_builddir(buildscript),
                    extra_env = self.extra_env)
    do_distclean.depends = [PHASE_CONFIGURE]

    def skip_uninstall(self, buildscript, last_phase):
        srcdir = self.get_srcdir(buildscript)
        if not os.path.exists(srcdir):
            return True
        if not os.path.exists(os.path.join(srcdir, self.makefile)):
            return True
        return False

    def do_uninstall(self, buildscript):
        buildscript.set_action(_('Uninstalling'), self)
        makeargs = self.makeargs + ' ' + self.config.module_makeargs.get(
                self.name, self.config.makeargs)
        cmd = '%s %s uninstall' % (os.environ.get('MAKE', 'make'), makeargs)
        buildscript.execute(cmd, cwd = self.get_builddir(buildscript),
                extra_env = self.extra_env)
        buildscript.packagedb.remove(self.name)

    def xml_tag_and_attrs(self):
        return ('autotools',
                [('autogenargs', 'autogenargs', ''),
                 ('id', 'name', None),
                 ('makeargs', 'makeargs', ''),
                 ('makeinstallargs', 'makeinstallargs', ''),
                 ('supports-non-srcdir-builds',
                  'supports_non_srcdir_builds', True),
                 ('skip-autogen', 'skip_autogen', False),
                 ('autogen-sh', 'autogen_sh', 'autogen.sh'),
                 ('makefile', 'makefile', 'Makefile'),
                 ('autogen-template', 'autogen_template', None)])


def parse_autotools(node, config, uri, repositories, default_repo):
    id = node.getAttribute('id')
    autogenargs = ''
    makeargs = ''
    makeinstallargs = ''
    supports_non_srcdir_builds = True
    autogen_sh = 'autogen.sh'
    skip_autogen = False
    check_target = True
    makefile = 'Makefile'
    autogen_template = None
    if node.hasAttribute('autogenargs'):
        autogenargs = node.getAttribute('autogenargs')
    if node.hasAttribute('makeargs'):
        makeargs = node.getAttribute('makeargs')
    if node.hasAttribute('makeinstallargs'):
        makeinstallargs = node.getAttribute('makeinstallargs')
    if node.hasAttribute('supports-non-srcdir-builds'):
        supports_non_srcdir_builds = \
            (node.getAttribute('supports-non-srcdir-builds') != 'no')
    if node.hasAttribute('skip-autogen'):
        skip_autogen = node.getAttribute('skip-autogen')
        if skip_autogen == 'true':
            skip_autogen = True
        elif skip_autogen == 'never':
            skip_autogen = 'never'
        else:
            skip_autogen = False
    if node.hasAttribute('check-target'):
        check_target = (node.getAttribute('check-target') == 'true')
    if node.hasAttribute('autogen-sh'):
        autogen_sh = node.getAttribute('autogen-sh')
    if node.hasAttribute('makefile'):
        makefile = node.getAttribute('makefile')
    if node.hasAttribute('autogen-template'):
        autogen_template = node.getAttribute('autogen-template')

    # Make some substitutions; do special handling of '${prefix}' and '${libdir}'
    p = re.compile('(\${prefix})')
    autogenargs     = p.sub(config.prefix, autogenargs)
    makeargs        = p.sub(config.prefix, makeargs)
    makeinstallargs = p.sub(config.prefix, makeinstallargs)
    # I'm not sure the replacement of ${libdir} is necessary for firefox...
    p = re.compile('(\${libdir})')
    libsubdir = '/lib'
    if config.use_lib64:
        libsubdir = '/lib64'
    autogenargs     = p.sub(config.prefix + libsubdir, autogenargs)
    makeargs        = p.sub(config.prefix + libsubdir, makeargs)
    makeinstallargs = p.sub(config.prefix + libsubdir, makeinstallargs)

    dependencies, after, suggests = get_dependencies(node)
    branch = get_branch(node, repositories, default_repo, config)

    return AutogenModule(id, branch, autogenargs, makeargs,
                         makeinstallargs=makeinstallargs,
                         dependencies=dependencies,
                         after=after,
                         suggests=suggests,
                         supports_non_srcdir_builds=supports_non_srcdir_builds,
                         skip_autogen=skip_autogen,
                         autogen_sh=autogen_sh,
                         makefile=makefile,
                         autogen_template=autogen_template,
                         check_target=check_target)
register_module_type('autotools', parse_autotools)

