-- TODO(organiser): stub Ownership Table (T4). The organisers supply the real mapping from
-- Component to Team (docs/decisions.md); T25 replaces this file with it. Every Team here is
-- fictional, chosen only so that Team-level answers can be built and tested before then.
--
-- One row per Component of both Datasets in the Base Corpus (10 BGL, 12 OpenStack); a corpus
-- test checks that the rows match the loaded Components exactly.
--
-- team_source says where each Team comes from: 'stub' on every row here
-- (datasets.OWNERSHIP_STUB_SOURCE). It travels with every join on the table, so an answer can tell
-- a stub Team from an organisers' one. T25 keeps the column with its own value and sets
-- datasets.OWNERSHIP_IS_STUB to False; a db test fails if the two disagree.
CREATE OR REPLACE TABLE ${slm}.ownership AS
SELECT
    CAST(dataset AS VARCHAR)   AS dataset,
    CAST(component AS VARCHAR) AS component,
    CAST(team AS VARCHAR)      AS team,
    CAST('stub' AS VARCHAR)    AS team_source
FROM (VALUES
    ('bgl', 'KERNEL',     'Kernel Team'),
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
