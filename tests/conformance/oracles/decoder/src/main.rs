use encoding_rs::Encoding;
use std::io::{self, Read, Write};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("read stdin");
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for line in input.lines() {
        if line.is_empty() {
            continue;
        }
        let mut parts = line.splitn(2, '\t');
        let label = parts.next().expect("label");
        let hex = parts.next().unwrap_or("");
        let bytes: Vec<u8> = (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).expect("hex byte"))
            .collect();
        let encoding = Encoding::for_label(label.as_bytes()).expect("known label");
        let (decoded, _) = encoding.decode_without_bom_handling(&bytes);
        let mut hexed = String::new();
        for byte in decoded.as_bytes() {
            hexed.push_str(&format!("{:02x}", byte));
        }
        writeln!(out, "{}", hexed).expect("write stdout");
    }
}
