# bugspots 0.2.2 (igrigorik/bugspots) scoring a repo, printed as JSON. No crapkit.
#
#   faketime -f '<newest commit time>' ruby bugspots_adapter.rb REPO BRANCH [DEPTH]
#
# Bugspots.scan walks BRANCH in topological order and scores every commit whose
# message matches the regex: 1 / (1 + e^(-12t + 12)), t running from the
# oldest fix (0) to now (1), where the fix's time is the commit time (committer
# date). The regex here is /./, so every commit with a message is a fix, which
# is how crapkit counts commits. Run under libfaketime frozen at the newest
# commit's time, "now" is the newest commit, where crapkit anchors its range.
# DEPTH keeps the first DEPTH commits of the walk (the gem's own depth).
# Each score is the gem's own sprintf('%.4f').
require "json"
require "bugspots"

depth = ARGV[2] ? Integer(ARGV[2]) : nil
fixes, spots = Bugspots.scan(ARGV[0], ARGV[1], depth, /./)
puts JSON.generate({
  "fixes" => fixes.map { |fix| { "date" => fix.date.to_i, "files" => fix.files } },
  "spots" => spots.map { |spot| [spot.file, spot.score] }
})
