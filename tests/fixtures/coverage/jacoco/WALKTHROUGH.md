# Walkthrough: a Gradle Java repo, cc-only to measured

`java-repo/` with its `pom.xml` left out is a Gradle Java repo: one `build.gradle`,
`Shapes.java` and `Greeter.java` under `src/main/java`, and one JUnit test. This is what
crapkit printed for it on 2026-11-03, before and after the scope got a JaCoCo lane. The
interpreter path crapkit prints is shortened to `crapkit`.

## Before: init leaves the lane commented

```
$ crapkit init
wrote crapkit.toml with 1 scope(s): src
every scope is cc-only (coverage_optional = true) and needs no lane; init left a commented
lane for java-gradle (gradle): uncomment it to measure, see
docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one - next: run `crapkit coverage`
```

The scope and the commented lane in `crapkit.toml`:

```toml
[[scope]]
name = "src"
paths = ["src"]
languages = ["java"]
coverage_optional = true

# # jacocoTestReport writes this XML only with reports { xml.required = true } in the build script
# [[lane]]
# name = "java-gradle"
# command = "gradle test jacocoTestReport"
# artifact = "build/reports/jacoco/test/jacocoTestReport.xml"
# parser = "jacoco"
# path_prefix = "src/main/java"
# scopes = ["src"]
```

The repo has no `gradlew`, so the command is `gradle`; with a root `gradlew` init writes
`./gradlew test jacocoTestReport`, or `gradlew test jacocoTestReport` under cmd.exe.

The scope scores on complexity alone, and the seed marks `grade` (ccn 8) at its ccn:

```
$ crapkit coverage
run 1 @ 81e8fe3d6d0: 8 functions scored: 8 cc-only, 1 over ceiling 6, CRAP load 20.0, grade D
$ crapkit worklist
  risk      0.0  ccn   8  crap     8.0  cov    -    1c/1a  src/main/java/com/acme/Shapes.java:34  Shapes::grade( int score , boolean curved , boolean late)
$ crapkit ratchet seed
crapkit-ratchet.tsv: added 1, tightened 0 - 1 mark(s) vs run 1 (81e8fe3d6d0)
```

## After: the lane is uncommented

The owner uncomments the lane and deletes `coverage_optional = true`. `build.gradle` already
sets `xml.required = true`. The report Gradle 8.10.2 wrote through that command
(`gradle-8.10.2.xml` here) sits at the lane's artifact path, so `--reuse-artifacts` reads it:

```
$ crapkit coverage --reuse-artifacts
run 2 @ efe30ee4850: 8 functions scored: 8 measured, 2 over ceiling 6, CRAP load 92.8, grade F
```

`verify` names the flip once for the scope, since the marks were taken cc-only:

```
$ crapkit verify
crapkit: ratchet marks were recorded under [crapkit-stamp=2 lizard=1.24.0] in
crapkit-ratchet.tsv, and the rules have moved since in 1 scope(s): src (coverage cc-only to
jacoco=1) - ... re-baseline with `crapkit ratchet seed --scope src`; src was cc-only and is
now measured by its jacoco lane(s), so its marks hold no coverage yet: follow
docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one
```

The scoped reseed takes `grade` from its cc-only 8 to its measured CRAP, and the worklist
now reads coverage from the report: `grade` never ran, and `logical` ran one of its four
branches.

```
$ crapkit ratchet seed --scope src
crapkit-ratchet.tsv: scope src: added 1, tightened 0, raised 1 (first src/main/java/com/acme/Shapes.java Shapes::grade( int score , boolean curved , boolean late) 8.0000 -> 72.0000) - 2 mark(s) vs run 2 (efe30ee4850)
$ crapkit worklist
  risk      0.0  ccn   8  crap    72.0  cov   0%    1c/1a  src/main/java/com/acme/Shapes.java:34  Shapes::grade( int score , boolean curved , boolean late)
  risk      0.0  ccn   3  crap     6.8  cov  25%    1c/1a  src/main/java/com/acme/Shapes.java:14  Shapes::logical( int a , int b)
```
