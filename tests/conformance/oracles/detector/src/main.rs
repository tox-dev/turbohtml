use chardetng::{EncodingDetector, Iso2022JpDetection, Utf8Detection};
use std::io::{self, Read, Write};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("read stdin");
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for line in input.lines() {
        let bytes: Vec<u8> = (0..line.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&line[i..i + 2], 16).expect("hex byte"))
            .collect();
        let mut detector = EncodingDetector::new(Iso2022JpDetection::Allow);
        detector.feed(&bytes, true);
        let guess = detector.guess(None, Utf8Detection::Allow);
        writeln!(out, "{}", guess.name()).expect("write stdout");
    }
}
