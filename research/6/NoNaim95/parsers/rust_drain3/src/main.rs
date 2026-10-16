//! usage: rd3bench INPUT OUTPREFIX SIM_TH DEPTH MASK(0|1)
//! Feeds each line of INPUT (one message per line) to drain3::Matcher::add_log_message in order,
//! writes OUTPREFIX.cid.i32 (little-endian i32 cluster id per line, -1 = error) and OUTPREFIX.templates.tsv.
use std::io::{BufRead, BufReader, Write};
use std::time::Instant;
use drain3::{Config, Matcher, MaskingInstruction, Template};

fn render(t: &Template) -> String {
    let mut it = t.tokens().iter();
    let mut out: Vec<String> = Vec::with_capacity(t.token_count());
    for i in 0..t.token_count() {
        if t.is_param(i) { out.push("<*>".into()) } else { out.push(it.next().map(|s| s.to_string()).unwrap_or_default()) }
    }
    out.join(" ")
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let (inp, out) = (&a[1], &a[2]);
    let sim: f64 = a[3].parse().unwrap();
    let depth: usize = a[4].parse().unwrap();
    let mask = a[5] == "1";
    // same intent as the Python tuned masks; Rust regex has no look-around, so boundaries are captured and re-emitted
    let masking = if mask { vec![
        MaskingInstruction { pattern: r"R\d{2}-M\d(?:-[A-Z0-9]+)*(?::[A-Z0-9]+(?:-[A-Z0-9]+)*)?".into(), mask: "<NODE>".into() },
        MaskingInstruction { pattern: r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}".into(), mask: "<UUID>".into() },
        MaskingInstruction { pattern: r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?(?:,\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?)*".into(), mask: "<IP>".into() },
        MaskingInstruction { pattern: r"0x[0-9a-fA-F]+".into(), mask: "<NUM>".into() },
        MaskingInstruction { pattern: r"(^|[^A-Za-z0-9])[0-9a-fA-F]{8,}([^A-Za-z0-9]|$)".into(), mask: "${1}<NUM>${2}".into() },
        MaskingInstruction { pattern: r"core\.\d+".into(), mask: "core.<NUM>".into() },
        MaskingInstruction { pattern: r"(^|[^A-Za-z0-9])-?\d+(?:\.\d+)?([^A-Za-z0-9]|$)".into(), mask: "${1}<NUM>${2}".into() },
    ] } else { vec![] };
    let cfg = Config::builder().similarity_threshold(sim).depth(depth).max_tokens(100000).max_bytes(1 << 24)
        .masking(masking).build();
    let f = BufReader::with_capacity(1 << 20, std::fs::File::open(inp).unwrap());
    let lines: Vec<String> = f.lines().map(|l| l.unwrap()).collect();
    let mut m = Matcher::new(cfg);
    let mut ids: Vec<i32> = Vec::with_capacity(lines.len());
    let mut last: Vec<Option<Template>> = Vec::new();
    let mut errs = 0usize;
    let t0 = Instant::now();
    for l in &lines {
        match m.add_log_message(l) {
            Ok(t) => { let id = t.id(); ids.push(id as i32); if last.len() <= id { last.resize(id + 1, None) } last[id] = Some(t); }
            Err(_) => { ids.push(-1); errs += 1 }
        }
    }
    let dt = t0.elapsed().as_secs_f64();
    let n_cl = last.iter().filter(|x| x.is_some()).count();
    let mut w = std::io::BufWriter::new(std::fs::File::create(format!("{}.cid.i32", out)).unwrap());
    for i in &ids { w.write_all(&i.to_le_bytes()).unwrap(); }
    let mut w2 = std::io::BufWriter::new(std::fs::File::create(format!("{}.templates.tsv", out)).unwrap());
    for (i, t) in last.iter().enumerate() { if let Some(t) = t { writeln!(w2, "{}\t{}", i, render(t)).unwrap(); } }
    println!("{{\"n\": {}, \"t_parse_s\": {:.3}, \"lines_per_s\": {:.0}, \"n_clusters\": {}, \"errors\": {}, \"sim_th\": {}, \"depth\": {}, \"mask\": {}}}",
        lines.len(), dt, lines.len() as f64 / dt, n_cl, errs, sim, depth, mask);
}
