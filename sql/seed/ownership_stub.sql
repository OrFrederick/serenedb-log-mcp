-- TODO(organiser): stub Ownership Table (T4). The organisers supply the real mapping from
-- Component to Team (docs/decisions.md); T25 replaces this file with it. Every Team here is
-- fictional, chosen only so that Team-level answers can be built and tested before then.
--
-- One row per Component of both Datasets in the Base Corpus. BGL also lists the four values the
-- dev loader reads from 318 mis-split kernel RAS lines ('0', 'a', 'iar', 'FATAL'), so that no
-- Component is unmapped.
CREATE OR REPLACE TABLE ${slm}.ownership AS
SELECT
    CAST(dataset AS VARCHAR)   AS dataset,
    CAST(component AS VARCHAR) AS component,
    CAST(team AS VARCHAR)      AS team
FROM (VALUES
    ('bgl', 'KERNEL',     'Kernel Team'),
    ('bgl', 'FATAL',      'Kernel Team'),
    ('bgl', '0',          'Kernel Team'),
    ('bgl', 'a',          'Kernel Team'),
    ('bgl', 'iar',        'Kernel Team'),
    ('bgl', 'APP',        'Application Runtime Team'),
    ('bgl', 'DISCOVERY',  'Control System Team'),
    ('bgl', 'MMCS',       'Control System Team'),
    ('bgl', 'CMCS',       'Control System Team'),
    ('bgl', 'BGLMASTER',  'Control System Team'),
    ('bgl', 'HARDWARE',   'Hardware Operations Team'),
    ('bgl', 'MONITOR',    'Hardware Operations Team'),
    ('bgl', 'LINKCARD',   'Interconnect Team'),
    ('bgl', 'SERV_NET',   'Interconnect Team'),
    ('openstack', 'nova.osapi_compute.wsgi.server',                     'Cloud API Team'),
    ('openstack', 'nova.api.openstack.wsgi',                            'Cloud API Team'),
    ('openstack', 'nova.api.openstack.compute.server_external_events',  'Cloud API Team'),
    ('openstack', 'nova.metadata.wsgi.server',                          'Cloud API Team'),
    ('openstack', 'keystonemiddleware.auth_token',                      'Identity Team'),
    ('openstack', 'nova.compute.manager',                               'Compute Service Team'),
    ('openstack', 'nova.compute.claims',                                'Compute Service Team'),
    ('openstack', 'nova.compute.resource_tracker',                      'Compute Service Team'),
    ('openstack', 'oslo_service.periodic_task',                         'Compute Service Team'),
    ('openstack', 'nova.virt.libvirt.driver',                           'Hypervisor Team'),
    ('openstack', 'nova.virt.libvirt.imagecache',                       'Hypervisor Team'),
    ('openstack', 'nova.scheduler.host_manager',                        'Scheduler Team')
) AS stub(dataset, component, team);
