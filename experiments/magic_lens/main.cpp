// Magic Lens / ROI Filter Push-Pull  -  C++20 + OpenCV CUDA
//
//  Thread 1 (CPU): webcam capture + hand-landmark query (MediaPipe sidecar) + state machine
//  Thread 2 (GPU): upload -> filter on ROI view (GpuMat) -> download -> overlay
//  Main thread   : display only (HighGUI must live on one thread)

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <exception>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/core/cuda.hpp>
#include <opencv2/cudaarithm.hpp>
#include <opencv2/cudafilters.hpp>
#include <opencv2/cudaimgproc.hpp>
#include <opencv2/highgui.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <opencv2/videoio.hpp>

namespace cfg {
inline constexpr const char* kSidecarHost = "127.0.0.1";
inline constexpr std::uint16_t kSidecarPort = 5555;

inline constexpr int kCamWidth = 1280;
inline constexpr int kCamHeight = 720;
inline constexpr int kTrackWidth = 320;   // frame size sent to MediaPipe
inline constexpr int kTrackHeight = 180;

inline constexpr double DIST_OPEN = 0.45;   // wrist distance / frame width
inline constexpr double DIST_CLOSE = 0.22;

inline constexpr float kMinRoi = 64.0F;
inline constexpr float kRoiSmoothing = 0.55F;  // EMA weight of the new sample
inline constexpr std::size_t kQueueCapacity = 2;
inline constexpr int kFilterCount = 3;
}  // namespace cfg

inline constexpr std::array<const char*, cfg::kFilterCount> kFilterNames{
    "THERMAL", "SKETCH", "BLUR"};

// ---------------------------------------------------------------------------
// Shared state (atomic state machine)
// ---------------------------------------------------------------------------
struct SharedState {
  std::atomic<int> current_filter_index{0};
  std::atomic<bool> hand_open{false};
  std::atomic<bool> running{true};
};

// ---------------------------------------------------------------------------
// Data passed between threads
// ---------------------------------------------------------------------------
struct Packet {
  cv::Mat frame;      // full-resolution BGR frame (mirrored)
  cv::Rect roi;       // valid only when has_roi
  bool has_roi{false};
};

struct Hand {
  std::array<cv::Point2f, 21> lm{};  // normalized [0,1]
};

// ---------------------------------------------------------------------------
// Bounded queue: mutex + condition_variable, drops the oldest item when full
// ---------------------------------------------------------------------------
template <class T>
class BoundedQueue {
 public:
  explicit BoundedQueue(std::size_t capacity) : capacity_(capacity) {}

  void push(T value) {
    {
      std::lock_guard lock(mutex_);
      if (closed_) return;
      if (queue_.size() >= capacity_) queue_.pop_front();
      queue_.push_back(std::move(value));
    }
    cond_.notify_one();
  }

  // Blocks until an item is available. Returns false once closed and drained.
  bool pop(T& out) {
    std::unique_lock lock(mutex_);
    cond_.wait(lock, [this] { return closed_ || !queue_.empty(); });
    if (queue_.empty()) return false;
    out = std::move(queue_.front());
    queue_.pop_front();
    return true;
  }

  void close() {
    {
      std::lock_guard lock(mutex_);
      closed_ = true;
    }
    cond_.notify_all();
  }

 private:
  std::mutex mutex_;
  std::condition_variable cond_;
  std::deque<T> queue_;
  std::size_t capacity_;
  bool closed_{false};
};

// Latest finished frame, consumed by the main (display) thread.
class LatestFrame {
 public:
  void set(cv::Mat m) {
    std::lock_guard lock(mutex_);
    frame_ = std::move(m);
  }
  std::optional<cv::Mat> take() {
    std::lock_guard lock(mutex_);
    if (frame_.empty()) return std::nullopt;
    cv::Mat out = std::move(frame_);
    frame_ = cv::Mat();
    return out;
  }

 private:
  std::mutex mutex_;
  cv::Mat frame_;
};

// ---------------------------------------------------------------------------
// MediaPipe sidecar client (TCP loopback, RAII socket)
//   -> uint32 (network order) length + JPEG bytes
//   <- uint8 n_hands, then n_hands * 42 little-endian float32 (x,y of 21 landmarks)
// ---------------------------------------------------------------------------
class SidecarClient {
 public:
  SidecarClient(const std::string& host, std::uint16_t port) {
    fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
    if (fd_ < 0) throw std::runtime_error("socket() failed");

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    if (::inet_pton(AF_INET, host.c_str(), &addr.sin_addr) != 1) {
      closeFd();
      throw std::runtime_error("invalid sidecar address");
    }
    if (::connect(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
      closeFd();
      throw std::runtime_error("cannot connect to hand_server.py");
    }
    int one = 1;
    ::setsockopt(fd_, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
  }
  ~SidecarClient() { closeFd(); }
  SidecarClient(const SidecarClient&) = delete;
  SidecarClient& operator=(const SidecarClient&) = delete;

  bool query(const cv::Mat& img, std::vector<Hand>& hands) {
    hands.clear();
    const std::vector<int> params{cv::IMWRITE_JPEG_QUALITY, 80};
    if (!cv::imencode(".jpg", img, jpg_, params)) return false;

    const std::uint32_t len = htonl(static_cast<std::uint32_t>(jpg_.size()));
    if (!sendAll(&len, sizeof(len)) || !sendAll(jpg_.data(), jpg_.size())) return false;

    std::uint8_t n = 0;
    if (!recvAll(&n, 1) || n > 2) return false;

    std::array<float, 42> buf{};
    for (std::uint8_t h = 0; h < n; ++h) {
      if (!recvAll(buf.data(), sizeof(float) * buf.size())) return false;
      Hand hand;
      for (int i = 0; i < 21; ++i) hand.lm[i] = {buf[2 * i], buf[2 * i + 1]};
      hands.push_back(hand);
    }
    return true;
  }

 private:
  void closeFd() {
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
  }
  bool sendAll(const void* data, std::size_t n) const {
    const auto* p = static_cast<const std::uint8_t*>(data);
    while (n > 0) {
      const ssize_t r = ::send(fd_, p, n, MSG_NOSIGNAL);
      if (r < 0 && errno == EINTR) continue;
      if (r <= 0) return false;
      p += r;
      n -= static_cast<std::size_t>(r);
    }
    return true;
  }
  bool recvAll(void* data, std::size_t n) const {
    auto* p = static_cast<std::uint8_t*>(data);
    while (n > 0) {
      const ssize_t r = ::recv(fd_, p, n, 0);
      if (r < 0 && errno == EINTR) continue;
      if (r <= 0) return false;
      p += r;
      n -= static_cast<std::size_t>(r);
    }
    return true;
  }

  int fd_{-1};
  std::vector<std::uint8_t> jpg_;
};

// ---------------------------------------------------------------------------
// GPU pipeline. Every filter runs on a GpuMat view of the ROI (no extra
// upload/crop copy); intermediate buffers are preallocated and reused.
// ---------------------------------------------------------------------------
class GpuPipeline {
 public:
  GpuPipeline()
      : pin_in_(cv::cuda::HostMem::PAGE_LOCKED),
        pin_out_(cv::cuda::HostMem::PAGE_LOCKED) {
    // Thermal LUT: 256x1 CV_8UC3 palette
    cv::Mat ramp(256, 1, CV_8UC1);
    for (int i = 0; i < 256; ++i) ramp.at<std::uint8_t>(i) = static_cast<std::uint8_t>(i);
    cv::Mat palette;
    cv::applyColorMap(ramp, palette, cv::COLORMAP_JET);
    lut_ = cv::cuda::createLookUpTable(palette);

    gauss_color_ = cv::cuda::createGaussianFilter(CV_8UC3, CV_8UC3, cv::Size(31, 31), 0.0);
    gauss_gray_ = cv::cuda::createGaussianFilter(CV_8UC1, CV_8UC1, cv::Size(5, 5), 0.0);
    canny_ = cv::cuda::createCannyEdgeDetector(50.0, 150.0, 3, false);
  }

  cv::Mat process(const cv::Mat& frame, const cv::Rect& roi, bool has_roi, int filter_index) {
    pin_in_.create(frame.rows, frame.cols, CV_8UC3);
    cv::Mat in_header = pin_in_.createMatHeader();
    frame.copyTo(in_header);

    d_frame_.upload(pin_in_, stream_);

    if (has_roi) {
      cv::cuda::GpuMat view(d_frame_, roi);  // shares device memory with d_frame_
      applyFilter(view, filter_index);
    }

    d_frame_.download(pin_out_, stream_);
    stream_.waitForCompletion();
    return pin_out_.createMatHeader().clone();
  }

 private:
  void applyFilter(cv::cuda::GpuMat& view, int index) {
    switch (index) {
      case 0: {  // THERMAL: gray -> 3ch -> LUT (palette)
        cv::cuda::cvtColor(view, d_gray_, cv::COLOR_BGR2GRAY, 0, stream_);
        cv::cuda::cvtColor(d_gray_, d_bgr_a_, cv::COLOR_GRAY2BGR, 0, stream_);
        lut_->transform(d_bgr_a_, d_bgr_b_, stream_);
        d_bgr_b_.copyTo(view, stream_);
        break;
      }
      case 1: {  // SKETCH: gray -> light blur -> Canny -> invert -> 3ch
        cv::cuda::cvtColor(view, d_gray_, cv::COLOR_BGR2GRAY, 0, stream_);
        gauss_gray_->apply(d_gray_, d_gray_b_, stream_);
        canny_->detect(d_gray_b_, d_edges_, stream_);
        cv::cuda::bitwise_not(d_edges_, d_gray_, cv::noArray(), stream_);
        cv::cuda::cvtColor(d_gray_, d_bgr_a_, cv::COLOR_GRAY2BGR, 0, stream_);
        d_bgr_a_.copyTo(view, stream_);
        break;
      }
      default: {  // BLUR: 31x31 Gaussian
        gauss_color_->apply(view, d_bgr_a_, stream_);
        d_bgr_a_.copyTo(view, stream_);
        break;
      }
    }
  }

  cv::cuda::Stream stream_;
  cv::cuda::HostMem pin_in_;
  cv::cuda::HostMem pin_out_;
  cv::cuda::GpuMat d_frame_, d_gray_, d_gray_b_, d_edges_, d_bgr_a_, d_bgr_b_;
  cv::Ptr<cv::cuda::LookUpTable> lut_;
  cv::Ptr<cv::cuda::Filter> gauss_color_;
  cv::Ptr<cv::cuda::Filter> gauss_gray_;
  cv::Ptr<cv::cuda::CannyEdgeDetector> canny_;
};

// ---------------------------------------------------------------------------
// Thread 1: capture + tracking + atomic state machine
// ---------------------------------------------------------------------------
void trackingThread(SharedState& st, BoundedQueue<Packet>& queue,
                    cv::VideoCapture& cap, SidecarClient& client) {
  cv::Mat frame, small;
  std::vector<Hand> hands;
  cv::Rect2f smooth;
  bool has_smooth = false;

  while (st.running.load(std::memory_order_acquire)) {
    if (!cap.read(frame) || frame.empty()) {
      std::cerr << "[tracking] camera read failed\n";
      break;
    }
    cv::flip(frame, frame, 1);  // mirror view; landmarks are computed on the mirrored image
    cv::resize(frame, small, cv::Size(cfg::kTrackWidth, cfg::kTrackHeight), 0, 0, cv::INTER_AREA);

    if (!client.query(small, hands)) {
      std::cerr << "[tracking] sidecar connection lost\n";
      break;
    }

    // Deterministic order: leftmost wrist first
    std::sort(hands.begin(), hands.end(),
              [](const Hand& a, const Hand& b) { return a.lm[0].x < b.lm[0].x; });

    const float fw = static_cast<float>(frame.cols);
    const float fh = static_cast<float>(frame.rows);
    const auto toPx = [&](const cv::Point2f& p) { return cv::Point2f(p.x * fw, p.y * fh); };

    Packet packet;

    // ROI from thumb tip (4) and index tip (8) of the lens hand
    if (!hands.empty()) {
      const cv::Point2f a = toPx(hands[0].lm[4]);
      const cv::Point2f b = toPx(hands[0].lm[8]);
      const cv::Point2f c = (a + b) * 0.5F;
      const float w = std::max(std::abs(a.x - b.x), cfg::kMinRoi);
      const float h = std::max(std::abs(a.y - b.y), cfg::kMinRoi);
      const cv::Rect2f target(c.x - w * 0.5F, c.y - h * 0.5F, w, h);

      if (!has_smooth) {
        smooth = target;
        has_smooth = true;
      } else {
        const float k = cfg::kRoiSmoothing;
        smooth.x += (target.x - smooth.x) * k;
        smooth.y += (target.y - smooth.y) * k;
        smooth.width += (target.width - smooth.width) * k;
        smooth.height += (target.height - smooth.height) * k;
      }
      const cv::Rect clipped = cv::Rect(cvRound(smooth.x), cvRound(smooth.y),
                                        cvRound(smooth.width), cvRound(smooth.height)) &
                               cv::Rect(0, 0, frame.cols, frame.rows);
      if (clipped.width >= 16 && clipped.height >= 16) {
        packet.roi = clipped;
        packet.has_roi = true;
      }
    } else {
      has_smooth = false;
    }

    // Push-pull state machine (needs both hands)
    if (hands.size() >= 2) {
      const cv::Point2f w0 = toPx(hands[0].lm[0]);
      const cv::Point2f w1 = toPx(hands[1].lm[0]);
      const double dist = cv::norm(w0 - w1) / static_cast<double>(frame.cols);
      const bool open = st.hand_open.load(std::memory_order_acquire);

      if (!open && dist > cfg::DIST_OPEN) {
        st.hand_open.store(true, std::memory_order_release);        // pulled apart -> armed
      } else if (open && dist < cfg::DIST_CLOSE) {
        const int next = (st.current_filter_index.load(std::memory_order_acquire) + 1) %
                         cfg::kFilterCount;
        st.current_filter_index.store(next, std::memory_order_release);
        st.hand_open.store(false, std::memory_order_release);       // pushed together -> next filter
      }
    }

    packet.frame = std::move(frame);
    frame = cv::Mat();  // detach: next cap.read allocates a fresh buffer
    queue.push(std::move(packet));
  }

  st.running.store(false, std::memory_order_release);
  queue.close();
}

// ---------------------------------------------------------------------------
// Thread 2: GPU processing + overlay
// ---------------------------------------------------------------------------
void gpuThread(SharedState& st, BoundedQueue<Packet>& queue, LatestFrame& out) {
  try {
    auto pipeline = std::make_unique<GpuPipeline>();
    Packet packet;
    double fps = 0.0;
    auto last = std::chrono::steady_clock::now();

    while (queue.pop(packet)) {
      const int idx = st.current_filter_index.load(std::memory_order_acquire);
      const bool open = st.hand_open.load(std::memory_order_acquire);

      cv::Mat result = pipeline->process(packet.frame, packet.roi, packet.has_roi, idx);

      const cv::Scalar color = open ? cv::Scalar(80, 220, 80) : cv::Scalar(0, 140, 255);
      if (packet.has_roi) cv::rectangle(result, packet.roi, color, 2, cv::LINE_AA);

      const auto now = std::chrono::steady_clock::now();
      const double dt = std::chrono::duration<double>(now - last).count();
      last = now;
      if (dt > 0.0) fps = fps == 0.0 ? 1.0 / dt : 0.9 * fps + 0.1 / dt;

      const std::string label = std::string("Filter: ") + kFilterNames[static_cast<std::size_t>(idx)] +
                                (open ? "  [OPEN]" : "  [CLOSED]") + "  " +
                                std::to_string(static_cast<int>(fps)) + " FPS";
      cv::putText(result, label, {16, 32}, cv::FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv::LINE_AA);

      out.set(std::move(result));
    }
  } catch (const cv::Exception& e) {
    std::cerr << "[gpu] OpenCV/CUDA error: " << e.what() << '\n';
  } catch (const std::exception& e) {
    std::cerr << "[gpu] error: " << e.what() << '\n';
  }
  st.running.store(false, std::memory_order_release);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
std::unique_ptr<SidecarClient> connectWithRetry(int seconds) {
  for (int i = 0; i < seconds * 2; ++i) {
    try {
      return std::make_unique<SidecarClient>(cfg::kSidecarHost, cfg::kSidecarPort);
    } catch (const std::exception&) {
      std::this_thread::sleep_for(std::chrono::milliseconds(500));
    }
  }
  return nullptr;
}

// Stops and joins threads on every exit path.
struct ThreadGuard {
  SharedState& st;
  BoundedQueue<Packet>& queue;
  std::vector<std::thread>& threads;
  ~ThreadGuard() {
    st.running.store(false, std::memory_order_release);
    queue.close();
    for (auto& t : threads)
      if (t.joinable()) t.join();
  }
};

// ---------------------------------------------------------------------------
int main() {
  try {
    if (cv::cuda::getCudaEnabledDeviceCount() <= 0) {
      std::cerr << "No CUDA device found (or OpenCV built without CUDA).\n";
      return EXIT_FAILURE;
    }
    cv::cuda::setDevice(0);

    auto cap = std::make_unique<cv::VideoCapture>(0);
    if (!cap->isOpened()) {
      std::cerr << "Cannot open webcam.\n";
      return EXIT_FAILURE;
    }
    cap->set(cv::CAP_PROP_FOURCC, cv::VideoWriter::fourcc('M', 'J', 'P', 'G'));
    cap->set(cv::CAP_PROP_FRAME_WIDTH, cfg::kCamWidth);
    cap->set(cv::CAP_PROP_FRAME_HEIGHT, cfg::kCamHeight);
    cap->set(cv::CAP_PROP_BUFFERSIZE, 1);

    std::cout << "Waiting for hand_server.py on " << cfg::kSidecarHost << ':'
              << cfg::kSidecarPort << " ...\n";
    auto client = connectWithRetry(30);
    if (!client) {
      std::cerr << "hand_server.py is not running. Start it first: python3 hand_server.py\n";
      return EXIT_FAILURE;
    }

    SharedState state;
    BoundedQueue<Packet> queue(cfg::kQueueCapacity);
    LatestFrame latest;

    std::vector<std::thread> threads;
    ThreadGuard guard{state, queue, threads};
    threads.emplace_back(trackingThread, std::ref(state), std::ref(queue), std::ref(*cap),
                         std::ref(*client));
    threads.emplace_back(gpuThread, std::ref(state), std::ref(queue), std::ref(latest));

    const std::string window = "Magic Lens";
    cv::namedWindow(window, cv::WINDOW_AUTOSIZE);

    while (state.running.load(std::memory_order_acquire)) {
      if (auto frame = latest.take()) {
        cv::imshow(window, *frame);
      } else {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
      const int key = cv::waitKey(1);
      if (key == 27 || key == 'q') break;
    }
    return EXIT_SUCCESS;  // ThreadGuard stops and joins; unique_ptrs release camera/socket
  } catch (const std::exception& e) {
    std::cerr << "Fatal: " << e.what() << '\n';
    return EXIT_FAILURE;
  }
}
