# bugspots 0.2.2 (igrigorik/bugspots) scoring a repo, printed as JSON. No crapkit.
#
#   faketime -f '<newest commit time>' ruby bugspots_adapter.rb REPO BRANCH [LIST]
#
# Bugspots.scan walks BRANCH in topological order and scores every commit whose
# message matches the regex: 1 / (1 + e^(-12t + 12)), t running from the
# last fix it walked (0) to now (1), where the fix's time is the commit time
# (committer date). The regex here is /./, so every commit with a message is a
# fix, which is how crapkit counts commits. Run under libfaketime frozen at the
# newest commit's time, "now" is the newest commit, where crapkit anchors its
# range.
#
# LIST names a file of commit ids, newest first. Given one, the walk yields
# those commits and no others: the gem cuts its walk with take(depth), and that
# call returns the listed commits here. The gem's own scoring runs unchanged.
# Each score is the gem's own sprintf('%.4f').
require "json"
require "bugspots"

depth = nil
if ARGV[2]
  LISTED_REPO = Rugged::Repository.new(ARGV[0])
  LISTED = File.readlines(ARGV[2], chomp: true).reject(&:empty?)
  depth = LISTED.size
  Rugged::Walker.prepend(Module.new do
    def take(_count)
      LISTED.map { |id| LISTED_REPO.lookup(id) }
    end
  end)
end

fixes, spots = Bugspots.scan(ARGV[0], ARGV[1], depth, /./)
puts JSON.generate({
  "fixes" => fixes.map { |fix| { "date" => fix.date.to_i, "files" => fix.files } },
  "spots" => spots.map { |spot| [spot.file, spot.score] }
})
