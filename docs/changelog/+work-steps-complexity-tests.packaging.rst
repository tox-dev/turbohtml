The complexity regression tests now count the C loop steps that a ``-Dwork_steps=true`` build records instead of timing
parses, so they no longer pass or fail with machine load. Release builds leave the option off and compile the counter
out.
