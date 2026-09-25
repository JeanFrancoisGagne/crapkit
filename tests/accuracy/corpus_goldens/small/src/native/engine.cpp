// Rvalue references and a template member (issue R15 shapes).
#include <string>
#include <utility>

class Engine {
public:
    void load(std::string &&name, bool warm)
    {
        if (warm && !name.empty()) {
            name_ = std::move(name);
        } else if (!warm) {
            name_ = "cold";
        }
    }

    template <typename T>
    T clampTo(T value, T low, T high) const
    {
        if (value < low) {
            return low;
        }
        return value > high ? high : value;
    }

private:
    std::string name_;
};
