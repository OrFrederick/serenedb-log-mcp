## cluster 2 (117 templates; components {'KERNEL': 110, 'APP': 2, 'FATAL': 1})
- E208 [KERNEL|-] instruction cache parity error corrected
- E94 [KERNEL|-] <*> ddr errors(s) detected and corrected on rank <*>, symbol <*> bit <*>
- E218 [KERNEL|-] <*> L3 <*> error(s) (dcr <*>) detected and corrected
- E191 [KERNEL|-] total of <*> ddr error(s) detected and corrected
- E95 [KERNEL|-] ddr: activating redundant bit steering: rank=<*> symbol=<*>
- E99 [KERNEL|-] <*> tree receiver <*> in re-synch state event(s) (dcr <*>) detected
- E285 [KERNEL|KERNDTLB] data TLB error interrupt
- E286 [KERNEL|KERNMC] machine check interrupt
- ... 109 more
## cluster 11 (33 templates; components {'APP': 18, 'KERNEL': 12, 'BGLMASTER': 2})
- E35 [APP|-] ciod: Error loading <*>: invalid or missing program image, No such file or directory
- E280 [MMCS|-] ciodb has been restarted.
- E118 [APP|-] ciod: LOGIN chdir(<*>) failed: No such file or directory
- E279 [KERNEL|-] ciod: Message code <*> is not <*> or <*>
- E80 [KERNEL|-] ciod: pollControlDescriptors: Detected the debugger died.
- E104 [KERNEL|-] ciod: for node <*> read continuation request but ioState is <*>
- E145 [KERNEL|-] ciod: cpu <*> at treeaddr <*> sent unrecognized message <*>
- E159 [APP|-] ciod: LOGIN chdir(<*>) failed: Permission denied
- ... 25 more
## cluster 5 (26 templates; components {'DISCOVERY': 11, 'HARDWARE': 7, 'KERNEL': 4})
- E86 [KERNEL|-] ddr: excessive soft failures, consider replacing the card
- E259 [DISCOVERY|-] Node card is not fully functional
- E167 [DISCOVERY|-] Can not get assembly information for node card
- E45 [DISCOVERY|-] Node card VPD check: <*> node in processor card slot <*> do not match. VPD ecid <*>, found
- E297 [HARDWARE|-] NodeCard VPD is corrupt
- E189 [KERNEL|-] rror threshold, consider replacing the card
- E166 [KERNEL|-] error threshold, consider replacing the card
- E120 [HARDWARE|-] NodeCard temperature sensor chip <*> is not accessible
- ... 18 more
## cluster 20 (23 templates; components {'KERNEL': 23})
- E314 [KERNEL|-] CE sym <*>, at <*>, mask <*>
- E247 [KERNEL|-] floating point instr. enabled.....<*>
- E262 [KERNEL|-] floating pt ex mode <*> enable......<*>
- E282 [KERNEL|-] symbol................<*>
- E281 [KERNEL|-] mask..................<*>
- E128 [KERNEL|-] floating point operation....................<*>
- E240 [KERNEL|-] <*> floating point alignment exceptions
- E196 [KERNEL|-] Floating Point Status and Control Register: <*>
- ... 15 more
## cluster 4 (22 templates; components {'KERNEL': 21, 'iar': 1})
- E237 [KERNEL|-] <*> double-hummer alignment exceptions
- E220 [KERNEL|-] summary...........................<*>
- E283 [KERNEL|-] miscompare............<*>
- E268 [KERNEL|-] exception syndrome register: <*>
- E127 [KERNEL|-] byte ordering exception.....................<*>
- E155 [KERNEL|-] exception summary........................<*>
- E185 [KERNEL|-] enabled exception summary................<*>
- E190 [KERNEL|-] invalid operation exception summary......<*>
- ... 14 more
## cluster 10 (14 templates; components {'KERNEL': 14})
- E301 [KERNEL|-] instruction address: <*>
- E233 [KERNEL|-] instruction address space.........<*>
- E242 [KERNEL|-] data address space................<*>
- E174 [KERNEL|-] disable store gathering..................<*>
- E203 [KERNEL|-] guaranteed instruction cache block touch.<*>
- E205 [KERNEL|-] guaranteed data cache block touch........<*>
- E183 [KERNEL|-] force load/store alignment...............<*>
- E169 [KERNEL|-] icache prefetch depth....................<*>
- ... 6 more
## cluster 8 (12 templates; components {'KERNEL': 12})
- E267 [KERNEL|-] core configuration register: <*>
- E269 [KERNEL|-] general purpose registers:
- E273 [KERNEL|-] special purpose registers:
- E313 [KERNEL|-] lr:<*> cr:<*> xer:<*> ctr:<*>
- E274 [KERNEL|-] L3 global control register: <*>
- E293 [KERNEL|-] L3 ecc control register: <*>
- E300 [KERNEL|-] L3 ecc status register: <*>
- E103 [KERNEL|-] regctl scancom interface.........................<*>
- ... 4 more
## cluster 13 (11 templates; components {'HARDWARE': 6, 'LINKCARD': 5})
- E93 [LINKCARD|-] MidplaneSwitchController performing bit sparing on <*> bit <*>
- E46 [HARDWARE|-] PrepareForService shutting down NodeCard(<*>, <*>, <*>, <*>, <*>) as part of Service Actio
- E30 [HARDWARE|-] EndServiceAction is restarting the NodeCards in midplane <*> as part of Service Action <*>
- E252 [HARDWARE|-] EndServiceAction <*> performed upon <*> by <*>
- E73 [LINKCARD|LINKDISC] MidplaneSwitchController::sendTrain() port disconnected: <*>
- E31 [HARDWARE|-] EndServiceAction is restarting the LinkCards in midplane <*> as part of Service Action <*>
- E57 [LINKCARD|LINKPAP] MidplaneSwitchController::parityAlignment() pap failed: <*> status=<*>
- E61 [LINKCARD|LINKIAP] MidplaneSwitchController::receiveTrain() iap failed: <*>, status=<*>
- ... 3 more
## cluster 9 (10 templates; components {'DISCOVERY': 6, 'KERNEL': 3, 'MMCS': 1})
- E288 [KERNEL|-] chip select...........<*>
- E219 [DISCOVERY|-] Ido chip status changed: <*> ip=<*> v=<*> t=<*> status=<*> <*>
- E133 [DISCOVERY|-] BglIdoChip table has <*> IDOs with the same IP address <*>
- E230 [DISCOVERY|-] SerialNumber(<*>) IpAddress(<*>) Status(<*>)
- E1 [DISCOVERY|-] Problem reading the ethernet arl entries fro the service card: <*>: while executing I2C Op
- E146 [DISCOVERY|-] New ido chip inserted into the database: <*> ip=<*> v=<*> t=<*>
- E307 [MMCS|-] Ido packet timeout
- E16 [DISCOVERY|-] Problem communicating with service card, ido chip: <*>. java.io.IOException: Could not fin
- ... 2 more
## cluster 0 (9 templates; components {'KERNEL': 3, 'BGLMASTER': 3, 'CMCS': 2})
- E158 [KERNEL|-] start initialization.....................<*>
- E154 [KERNEL|-] start flushing...........................<*>
- E105 [KERNEL|-] command manager unit summary.....................<*>
- E272 [CMCS|-] Starting SystemController
- E266 [CMCS|-] Running as background command
- E32 [BGLMASTER|-] BGLMaster has been started: ./BGLMaster --consoleip <*> --consoleport <*> --configfile <*>
- E22 [BGLMASTER|-] BGLMaster has been started: ./BGLMaster --consoleip <*> --consoleport <*> --configfile <*>
- E14 [MMCS|-] mmcs_db_server has been started: ./mmcs_db_server --useDatabase <*> --dbproperties <*> --i
- ... 1 more
## cluster 1 (7 templates; components {'KERNEL': 7})
- E310 [KERNEL|-] generating core.<*>
- E256 [KERNEL|-] problem state (<*>=sup,<*>=usr).......<*>
- E96 [KERNEL|-] state machine....................................<*>
- E318 [KERNEL|-] <*>, max=<*>
- E319 [KERNEL|-] ax=<*>
- E320 [KERNEL|-] x=<*>
- E316 [KERNEL|-] program
## cluster 22 (7 templates; components {'KERNEL': 7})
- E197 [KERNEL|-] disable apu instruction broadcast........<*>
- E171 [KERNEL|-] disable trace broadcast..................<*>
- E177 [KERNEL|-] disable speculative access...............<*>
- E210 [KERNEL|-] disable all access to cache directory....<*>
- E198 [KERNEL|-] write buffer commit threshold............<*>
- E217 [KERNEL|-] disable write lines <*>..................<*>
- E181 [KERNEL|-] enable non-IEEE mode.....................<*>
## cluster 18 (5 templates; components {'MMCS': 2, 'KERNEL': 2, 'BGLMASTER': 1})
- E98 [MMCS|-] idoproxydb has been started: $Name: <*> $ Input parameters: <*>
- E55 [KERNEL|KERNSOCK] MailboxMonitor::serviceMailboxes() lib_ido_error: <*> socket closed
- E224 [BGLMASTER|-] idoproxy exited normally with exit code <*>
- E26 [MMCS|-] idoproxydb hit ASSERT condition: ASSERT expression=<*> Source file=<*> Source line=<*> Fun
- E134 [KERNEL|KERNSOCK] idoproxy communication failure: socket closed
## cluster 16 (4 templates; components {'KERNEL': 4})
- E257 [KERNEL|KERNMNT] Error: unable to mount filesystem
- E211 [KERNEL|KERNMNTF] Lustre mount FAILED : <*> : block_id : location
- E165 [KERNEL|-] NFS Mount failed on <*>, slept <*> seconds, retrying (<*>)
- E276 [KERNEL|KERNMNTF] Lustre mount FAILED : <*> : point <*>
## cluster 7 (3 templates; components {'KERNEL': 3})
- E161 [KERNEL|-] start retagging..........................<*>
- E311 [KERNEL|-] Target=<*> Message=<*>
- E284 [KERNEL|-] Target=<*> Message=<*> JtagId = <*>
## cluster 17 (3 templates; components {'SERV_NET': 3})
- E41 [SERV_NET|-] DeclareServiceNetworkCharacteristics has been run but the DB is not empty
- E23 [SERV_NET|-] DeclareServiceNetworkCharacteristics has been run with the force option but the DB is not 
- E263 [SERV_NET|-] Added <*> subnets and <*> addresses to DB
## cluster 12 (2 templates; components {'KERNEL': 2})
- E215 [KERNEL|-] close EDRAM pages as soon as possible....<*>
- E305 [KERNEL|-] shutdown complete
## cluster 3 (2 templates; components {'KERNEL': 1, 'HARDWARE': 1})
- E200 [KERNEL|-] size of scratchpad portion of L3.........<*> (<*>)
- E7 [HARDWARE|-] <*> with VPD of <*>: IBM Part Number: <*>, Vendor: <*>, Vendor Serial Number: <*>, Assembl
## cluster 6 (2 templates; components {'KERNEL': 2})
- E193 [KERNEL|-] turn on hidden refreshes.................<*>
- E206 [KERNEL|-] quiet <*>................................<*>
## cluster 19 (2 templates; components {'KERNEL': 2})
- E287 [KERNEL|-] capture valid.........<*>
- E298 [KERNEL|KERNMICRO] Microloader Assertion
## cluster 24 (2 templates; components {'KERNEL': 2})
- E304 [KERNEL|KERNPOW] Power deactivated: <*>
- E66 [KERNEL|KERNSERV] Power Good signal deactivated: <*>. A service action may be required.
## cluster 21 (1 templates; components {'KERNEL': 1})
- E289 [KERNEL|-] qw trapped............<*>
## cluster 14 (1 templates; components {'CMCS': 1})
- E291 [CMCS|-] Controlling BG/L rows [ <*> ]
## cluster 15 (1 templates; components {'KERNEL': 1})
- E317 [KERNEL|-] iar <*> dear <*>
## cluster 23 (1 templates; components {'KERNEL': 1})
- E44 [KERNEL|-] L1 DCACHE summary averages: #ofDirtyLines: <*> out of <*> #ofDirtyDblWord: <*> out of <*>
