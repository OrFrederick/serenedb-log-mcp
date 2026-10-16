"""Hand 'problem family' labels for every template (auditable rules; first match wins).
Writes r7e_families.yaml. Used as one reference labelling for the clustering experiment."""
import re, sys, os, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, HERE
BGL_RULES = [
    ("fp_register_dump", r"Floating Point|exception summary|round (nearest|toward)|^(plus|minus) |quiet |fraction |enable .*exceptions|invalid operation|underflow exception|divide-by-zero exception|inexact exception|^reserved\.|^invalid \(|non-IEEE"),
    ("filesystem", r"mount|Lustre|NFS|chdir"),
    ("power_thermal", r"[Pp]ower|[Tt]emperature [Oo]ver|temperature sensor|PGOOD"),
    ("network_torus_tree_link", r"torus|tree|Torus|[Ll]ink[Cc]ard|link card|MidplaneSwitch|socket|ethernet|CioStream|[Cc]able|wire|Ido packet|no ethernet"),
    ("l3_edram", r"L3|EDRAM|in directory|scratchpad|retagging|start (initialization|flushing|prefetching)|speculative|cache directory|write buffer|hidden refreshes|write lines|DDR we are caching|DDR UE|parity error in (bank|read queue)|interrupt threshold\.|EDRAM pages|outstanding prefetches|prefetch depth for core"),
    ("ddr_memory", r"\bddr\b|\bDDR\b|CE sym|memory manager|memory and bus|command manager|state machine\.|regctl|capture valid|^symbol\.|^mask\.|^miscompare|^address parity|^correctable error\.|qw trapped|chip select|correctable ddr|rror threshold"),
    ("cache_tlb_parity", r"cache.*parity|parity error|i-cache|d-cache|DCACHE|plb error|tlb error|TLB|L2 (DCU|dcache)"),
    ("cpu_exception", r"program interrupt|alignment exceptions|illegal|privileged|trap instr|operation\.\.\.|byte ordering|auxiliary|data store interrupt|store operation|data storage interrupt|exception syndrome|i-fetch|^program$"),
    ("cpu_register_dump", r"register|[Rr]egisters|machine state|wait state|interrupt enable|problem state|instr\. enabled|floating pt ex mode|debug |address space|store gathering|apu instruction|trace broadcast|guaranteed|force load|icache prefetch|lr:|msr=|dbcr0|^iar |instruction address|summary\.\.\.|interrupt \(unit|DCR read timeout|PLB write|suppressing further|total interrupts"),
    ("kernel_crash", r"panic|kernel terminated|rts internal|^machine check interrupt|Microloader|shutdown complete|rts: bad message|Target="),
    ("control_daemons_ciodb", r"ciodb|idoproxy"),
    ("app_job_ciod", r"ciod|program image|job |core\.|core files"),
    ("hw_service_discovery", r"ServiceAction|PrepareForService|NodeCard|Node card|node card|VPD|ecid|assembly|IDO|ido chip|Ido chip|SerialNumber|service card"),
    ("control_daemons", r"started|restarted|exited|SystemController|Controlling|background command|BGLMaster|idoproxy|mmcs|ciodb|DeclareService|subnets"),
]
OS_FAM = {
    "http_api": "E42 E41 E39 E15", "lifecycle_power_sync": "E25 E23 E26 E22 E4 E3",
    "spawn_build": "E37 E18 E9 E21 E38", "delete_teardown": "E34 E17 E30 E35 E8 E12",
    "resource_claims": "E13 E28 E29 E33 E11 E27 E10 E36", "resource_tracker": "E16 E20 E2 E32 E14 E1",
    "image_cache": "E44 E5 E47 E46 E43 E40 E31", "scheduler_sync": "E19 E7", "auth_keystone": "E24 E45 E6", "periodic_task_error": "E48",
}
R, _ = load(); fam = {}
os_map = {e: f for f, es in OS_FAM.items() for e in es.split()}
for r in R:
    if r["dataset"] == "BGL":
        f = next((n for n, rx in BGL_RULES if re.search(rx, r["template"])), "other").replace("control_daemons_ciodb", "control_daemons")
    else:
        f = os_map.get(r["event_id"], "other")
    fam[f"{r['dataset']}:{r['event_id']}"] = f
if __name__ == "__main__":
    yaml.safe_dump({"rules_bgl": dict(BGL_RULES), "openstack": OS_FAM, "assignment": fam}, open(f"{HERE}/r7e_families.yaml", "w"), sort_keys=False, width=200)
    import collections; print(collections.Counter(fam.values()))
    for r in R:
        if r["dataset"] == "BGL" and fam[f"BGL:{r['event_id']}"] == "other": print("OTHER", r["event_id"], r["template"][:90])
