use chardetng::{EncodingDetector, Iso2022JpDetection, Utf8Detection};
use std::io::{self, Read, Write};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("read stdin");
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for line in input.lines() {
        // "<hex>" guesses with no TLD hint; "<hex>\t<label>" hands the label to guess()
        let (hex, tld) = match line.split_once('\t') {
            Some((hex, label)) => (hex, Some(label.as_bytes())),
            None => (line, None),
        };
        let bytes: Vec<u8> = (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).expect("hex byte"))
            .collect();
        let mut detector = EncodingDetector::new(Iso2022JpDetection::Allow);
        detector.feed(&bytes, true);
        let guess = detector.guess(tld, Utf8Detection::Allow);
        writeln!(out, "{}", guess.name()).expect("write stdout");
    }
}
